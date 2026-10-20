"""Read-only, source-scoped player analytics; no personal-output cache/schema."""
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import heapq
import json
import math
import re
from pathlib import Path
import sqlite3
import statistics
import time
from zoneinfo import ZoneInfo

from .fast_profile_extract import extract_game, time_control
from .fast_profile_models import metric, rate
from .positions import MAX_PGN_BYTES, UnsupportedVariant
from .position_schema import check_version

SECTIONS = {'all', 'overview', 'trends', 'repertoire', 'time-management', 'tendencies', 'coverage'}


@contextmanager
def profile_reader(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=1)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        yield conn
    finally:
        conn.rollback()
        conn.close()


def complete_date(value):
    if not value:
        return None
    try:
        # Reject permissive/noncanonical PGN dates rather than replacing unknowns.
        parsed = date.fromisoformat(value.replace('.', '-'))
        return parsed if value.replace('.', '-') == parsed.isoformat() else None
    except (ValueError, TypeError):
        return None



def is_partial_date(value):
    if not value or value == '????.??.??': return False
    match = re.fullmatch(r'(\d{4}|[?]{4})[.](\d{2}|[?]{2})[.](\d{2}|[?]{2})', value)
    if not match or '?' not in value: return False
    year, month, day = match.groups()
    y = 2000 if year == '????' else int(year)
    m = 1 if month == '??' else int(month)
    d = 1 if day == '??' else int(day)
    try:
        date(y, m, d)
        return True
    except ValueError:
        return False


def month_key(value):
    full = complete_date(value)
    if full: return full.strftime('%Y-%m')
    if value and re.fullmatch(r'\d{4}[.]\d{2}[.](?:\d{2}|[?][?])', value) and (value.endswith('??') or complete_date(value)):
        try:
            return date(int(value[:4]), int(value[5:7]), 1).strftime('%Y-%m')
        except ValueError:
            pass
    return None


def outcome(result, white):
    if result == '1-0': return 'wins' if white else 'losses'
    if result == '0-1': return 'losses' if white else 'wins'
    if result == '1/2-1/2': return 'draws'
    return 'unfinished' if result == '*' else 'unknown'


def outcomes(counts):
    counts = dict(counts)
    counts = {k: int(counts.get(k, 0)) for k in ('wins', 'losses', 'draws', 'unfinished', 'unknown')}
    total = sum(counts.values()); completed = counts['wins']+counts['losses']+counts['draws']
    return {'total_games': total, **counts, 'completed_games': completed,
            'provenance': 'OBSERVED', 'win_rate': rate(counts['wins'], completed, games=completed, method='player_wins_over_completed_v1'),
            'score_rate': rate(counts['wins']+0.5*counts['draws'], completed, games=completed, method='player_points_over_completed_v1')}


class Samples:
    """Exact counts/mean/fixed duration bins; bound optional exact median storage."""
    def __init__(self):
        self.n = 0; self.total = 0.0; self.values = []; self.bins = Counter()
    def add(self, value):
        self.n += 1; self.total += value
        if len(self.values) < 4096: self.values.append(value)
        for bound in (1, 2, 5, 10, 30, 60, 120, math.inf):
            if value <= bound:
                self.bins[str(bound)] += 1; break
    def report(self, games, eligible, *, heuristic=False, method='reported_elapsed_v1', partial=False):
        return {'mean_seconds': metric(self.total/self.n if self.n else None, 'seconds', method,
                                      eligible=eligible, observed=self.n, games=games, moves=self.n, heuristic=heuristic, partial=partial),
                'median_seconds': metric(statistics.median(self.values) if 0 < self.n <= 4096 else None, 'seconds', method,
                                        eligible=eligible, observed=self.n, games=games, moves=self.n, heuristic=heuristic, unsupported=self.n > 4096,
                                        limitations=['Exact median omitted above 4096 observations; mean and fixed bins include every observation.'] if self.n > 4096 else []),
                'duration_bins_upper_inclusive_seconds': dict(self.bins), 'observations': self.n}


def selector_sql(source, username, color='either', date_from=None, date_to=None, time_control_filter=None):
    if not source or source != source.strip() or len(source) > 100 or not username or username != username.strip() or len(username) > 200:
        raise ValueError('Explicit source and exact nonempty player label required')
    if color not in ('white', 'black', 'either'):
        raise ValueError('Invalid player color')
    for value in (date_from, date_to):
        if value and (complete_date(value) is None or '.' in value): raise ValueError('Dates must be ISO YYYY-MM-DD')
    if date_from and date_to and date_from > date_to: raise ValueError('date_from must precede date_to')
    collation = 'NOCASE' if source in ('chess.com', 'lichess') else 'BINARY'
    params = [username, username, source]; extra = ''
    if date_from or date_to:
        extra += " AND length(g.date)=10 AND substr(g.date,1,4)>='0001' AND date(replace(g.date,'.','-'),'+0 days')=replace(g.date,'.','-')"
    for value, operator in ((date_from, '>='), (date_to, '<=')):
        if value:
            extra += f" AND replace(g.date,'.','-') {operator} ?"; params.append(value)
    if time_control_filter is not None:
        extra += ' AND g.time_control=?'; params.append(time_control_filter)
    side = '' if color == 'either' else (' AND w=1' if color == 'white' else ' AND b=1')
    cte = f'''WITH candidates AS (
        SELECT g.*, (g.white=? COLLATE {collation}) AS w, (g.black=? COLLATE {collation}) AS b
        FROM games g WHERE g.source=? {extra}),
        matched AS (SELECT * FROM candidates WHERE w OR b),
        selected AS (SELECT * FROM matched WHERE w!=b {side}) '''
    return cte, params, {'source': source, 'username': username, 'color': color, 'date_from': date_from,
                         'date_to': date_to, 'time_control': time_control_filter, 'matching': 'exact_ascii_nocase' if collation == 'NOCASE' else 'exact_case_sensitive'}


@lru_cache(maxsize=1)
def _load_book(path, stamp, size):
    from .openings import OpeningDatabase
    text = Path(path).read_bytes()
    entries = json.loads(text)
    limit = max(25, max((len(entry.get('uci', [])) for entry in entries), default=0))
    return OpeningDatabase(openings_data=entries), hashlib.sha256(text).hexdigest(), limit


def opening_book():
    root = Path(__file__).resolve().parents[1]/'data'
    path = root/'openings.json'
    if path.exists():
        stat = path.stat()
        return _load_book(str(path), stat.st_mtime_ns, stat.st_size)
    from .openings import OpeningDatabase
    # Preserve the existing TSV/minimal fallback behavior without CWD dependence.
    return OpeningDatabase(json_path=str(path), tsv_path=str(root/'openings.tsv')), 'tsv_or_minimal_fallback', None


def position_section(conn, cte, params, n, top_k):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='position_index_meta'").fetchone()
    coverage = {'schema_available': bool(exists), 'total_games': n, 'indexed_games': 0,
                'complete_games': 0, 'missing_games': n, 'pending_games': 0, 'failed_games': 0,
                'unsupported_games': 0, 'partial_index': bool(n), 'absence_is_conclusive': not n}
    if not exists:
        return {'coverage': coverage, 'frequent_reached': [], 'player_decisions': [], 'continuations': [],
                'overlap': metric(None, method='early_shared_position_game_fraction_v1', eligible=n, games=0)}
    try: check_version(conn)
    except ValueError as exc: raise sqlite3.DatabaseError('Incompatible position index version') from exc
    states = conn.execute(cte+'''SELECT s.status,count(*) AS n,
        sum(s.indexed_version=1 AND s.indexed_fingerprint=g.fingerprint) AS indexed
        FROM selected g JOIN game_position_index s USING(game_id) GROUP BY s.status''', params).fetchall()
    coverage['missing_games'] = n-sum(r['n'] for r in states)
    coverage['indexed_games'] = sum(r['indexed'] or 0 for r in states)
    for r in states: coverage[r['status'].lower()+'_games'] = r['n']
    coverage['partial_index'] = coverage['complete_games'] != n or coverage['indexed_games'] != n
    coverage['absence_is_conclusive'] = not coverage['partial_index']
    def top(decisions=False):
        own = " AND substr(p.canonical_fen,instr(p.canonical_fen,' ')+1,1)=CASE WHEN g.w THEN 'w' ELSE 'b' END" if decisions else ''
        return [dict(r) for r in conn.execute(cte+'''SELECT p.position_id,p.canonical_fen,
            count(*) AS occurrence_count,count(DISTINCT o.game_id) AS distinct_game_count,
            sum(o.next_move_uci IS NOT NULL) AS outgoing_occurrence_count,min(o.ply) AS first_ply
            FROM selected g JOIN position_occurrences o USING(game_id) JOIN positions p USING(position_id)
            WHERE p.identity_version=1 AND p.variant='standard' AND o.ply BETWEEN 1 AND 24 '''+own+'''
            GROUP BY p.position_id ORDER BY distinct_game_count DESC,p.position_id LIMIT ?''', params+[top_k])]
    reached, decisions = top(), top(True)
    continuations = []
    for pos in decisions[:5]:
        rows = conn.execute(cte+''' , per_game AS (
            SELECT o.next_move_uci AS uci,g.game_id,g.w,g.result,count(*) AS visits
            FROM selected g JOIN position_occurrences o USING(game_id)
            WHERE o.position_id=? AND g.w=? AND o.ply BETWEEN 1 AND 24 AND o.next_move_uci IS NOT NULL
            GROUP BY o.next_move_uci,g.game_id)
            SELECT uci,sum(visits) AS visits,count(*) AS games,
            sum((result='1-0' AND w=1) OR (result='0-1' AND w=0)) AS wins,
            sum((result='0-1' AND w=1) OR (result='1-0' AND w=0)) AS losses,
            sum(result='1/2-1/2') AS draws,sum(result='*') AS unfinished,
            sum(result NOT IN ('1-0','0-1','1/2-1/2','*')) AS unknown
            FROM per_game GROUP BY uci ORDER BY visits DESC,uci''', params+[pos['position_id'], int(pos['canonical_fen'].split()[1] == 'w')]).fetchall()
        denominator = sum(r['visits'] for r in rows)
        continuations.append({'position_id': pos['position_id'], 'perspective': 'selected_player',
                              'outgoing_occurrences': denominator, 'items': [
            {'uci': r['uci'], 'occurrence_count': r['visits'], 'distinct_game_count': r['games'],
             'move_frequency': rate(r['visits'], denominator, games=r['games'], partial=coverage['partial_index']),
             'outcomes': outcomes(r)} for r in rows[:top_k]]})
    return {'coverage': coverage, 'window': {'min_ply': 1, 'max_ply': 24, 'starting_occurrences_excluded': True},
            'frequent_reached': reached, 'player_decisions': decisions, 'continuations': continuations,
            'overlap': [{'position_id': p['position_id'], 'game_fraction': rate(p['distinct_game_count'], coverage['indexed_games'],
                        games=coverage['indexed_games'], method='early_shared_position_game_fraction_v1', partial=coverage['partial_index'])} for p in reached]}


def build_fast_profile(conn, source, username, *, color='either', date_from=None, date_to=None,
                       time_control_filter=None, section='all', top_k=10, max_games=20000,
                       max_seconds=30, clock_convention='unverified', today=None):
    """Caller supplies query-only SQLite connection and one read snapshot."""
    if section not in SECTIONS or not 1 <= top_k <= 50 or not 1 <= max_games <= 20000 or not math.isfinite(max_seconds) or not 0 < max_seconds <= 30:
        raise ValueError('Invalid section or request budget')
    if clock_convention not in ('unverified', 'post_increment'):
        raise ValueError('Clock convention must be unverified or post_increment')
    if not conn.execute('PRAGMA query_only').fetchone()[0] or not conn.in_transaction:
        raise RuntimeError('Fast profiles require a query-only read snapshot')
    started = time.monotonic()
    cte, params, selector = selector_sql(source, username, color, date_from, date_to, time_control_filter)
    def query(sql, extra=()): return conn.execute(cte+sql, params+list(extra))
    matched, ambiguous = query('SELECT count(*),coalesce(sum(w AND b),0) FROM matched').fetchone()
    if not matched: raise KeyError('No matching player label in this source')
    totals = Counter(); by_color = {'white': Counter(), 'black': Counter()}
    for r in query('SELECT w,result,count(*) AS n FROM selected GROUP BY w,result'):
        key = outcome(r['result'], r['w']); totals[key] += r['n']; by_color['white' if r['w'] else 'black'][key] += r['n']
    n = sum(totals.values())
    metadata = Counter(); monthly = defaultdict(Counter); controls = defaultdict(Counter); opponent_bands = defaultdict(Counter)
    ratings = defaultdict(list); rating_series = defaultdict(list); recent = []; recent_calendar = Counter()
    day = today or datetime.now(ZoneInfo('Asia/Kolkata')).date(); recent_from = day-timedelta(days=29)
    replay = section in ('all', 'repertoire', 'time-management', 'tendencies')
    book, book_checksum, opening_limit = opening_book() if section in ('all', 'repertoire') else (None, 'not_requested', None)
    coverage = {'matched_games': matched, 'eligible_games': n, 'ambiguous_identity_games': ambiguous,
                'replay_requested': replay, 'processed_games': 0, 'replayed_games': 0, 'replay_failed_games': 0, 'unsupported_games': 0,
                'clock_moves': None if not replay else 0, 'emt_moves': None if not replay else 0}
    counts = Counter(); phase_counts = Counter(); captured = Counter(); imbalances = Counter(); clock_counts = Counter()
    opening_examples = defaultdict(list); opening_groups = defaultdict(Counter); opening_trends = defaultdict(Counter); move_orders = Counter(); structures = Counter()
    opening_sources = Counter(); opening_confidence = Counter(); known_openings = Counter(); reported_ecos = Counter()
    duration_groups = defaultdict(Samples); phase_durations = defaultdict(Samples); last_five = Samples()
    time_by_control = defaultdict(Samples); checkpoints = defaultdict(Samples); durations_games = set()
    timing_games = defaultdict(set); timing_eligible = Counter(); checkpoint_eligible = Counter()
    development = []; queen_times = []; castled = queens_removed = standard_games = moves_games = 0
    errors = []; budget_exhausted = False
    has_openings = conn.execute("SELECT 1 FROM sqlite_master WHERE name='game_openings'").fetchone()
    join = ' LEFT JOIN game_openings op ON op.game_id=g.game_id ' if has_openings else ''
    opening_column = 'op.opening_data' if has_openings else 'NULL'
    pgn_column = f'CASE WHEN length(CAST(g.pgn AS BLOB))<={MAX_PGN_BYTES} THEN g.pgn END' if replay else 'NULL'
    rows = query(f'''SELECT g.game_id,g.w,g.result,g.date,g.white_rating,g.black_rating,g.time_control,
        {pgn_column} AS pgn,
        {opening_column} AS opening_data FROM selected g {join} ORDER BY g.game_id''')
    for g in rows:
        # Metadata is always complete; replay budgets never silently truncate its denominators.
        white = bool(g['w']); side = 'white' if white else 'black'; result = outcome(g['result'], white)
        played = complete_date(g['date']); month = month_key(g['date']); tc_raw = g['time_control']
        metadata['date_missing'] += not g['date'] or g['date'] == '????.??.??'
        metadata['date_partial'] += is_partial_date(g['date'])
        metadata['date_invalid'] += bool(g['date']) and g['date'] != '????.??.??' and played is None and not is_partial_date(g['date'])
        metadata['valid_dates'] += played is not None; metadata['month_dates'] += month is not None
        tc_key = tc_raw if time_control(tc_raw) else ('Unknown' if not tc_raw or tc_raw == '?' else ('Unlimited' if tc_raw == '-' else 'Unsupported: '+str(tc_raw)))
        controls[tc_key][result] += 1
        rating = g['white_rating' if white else 'black_rating']; opponent = g['black_rating' if white else 'white_rating']
        valid_rating = isinstance(rating, int) and rating > 0
        valid_opponent = isinstance(opponent, int) and opponent > 0
        metadata['player_rating_missing'] += rating is None
        metadata['opponent_rating_missing'] += opponent is None
        metadata['player_rating_invalid'] += rating is not None and not valid_rating
        metadata['opponent_rating_invalid'] += opponent is not None and not valid_opponent
        metadata['time_control_missing'] += not tc_raw or tc_raw == '?'
        metadata['time_control_unlimited'] += tc_raw == '-'
        metadata['time_control_inference_unsupported'] += bool(tc_raw) and tc_raw not in ('?', '-') and time_control(tc_raw) is None
        metadata['player_ratings'] += valid_rating; metadata['opponent_ratings'] += valid_opponent
        metadata['supported_time_controls'] += time_control(tc_raw) is not None
        if valid_opponent:
            band = '<1000' if opponent < 1000 else ('>=2400' if opponent >= 2400 else f'{opponent//200*200}-{opponent//200*200+199}')
        else: band = 'Unknown'
        opponent_bands[band][result] += 1
        if month:
            monthly[month][result] += 1
            if valid_rating: rating_series[(tc_key, month)].append(rating)
        if valid_rating: ratings[tc_key].append(rating)
        metadata['future_dates'] += played is not None and played > day
        if played and played <= day:
            value = (played.isoformat(), g['game_id'], result)
            heapq.heappush(recent, value)
            if len(recent) > 20: heapq.heappop(recent)
            if recent_from <= played <= day: recent_calendar[result] += 1
        if not replay: continue
        if coverage['processed_games'] >= max_games or time.monotonic()-started >= max_seconds:
            budget_exhausted = True; continue
        coverage['processed_games'] += 1
        try:
            stored = None
            if g['opening_data']:
                try: stored = json.loads(g['opening_data'])
                except (TypeError, ValueError): metadata['invalid_stored_opening'] += 1
                if not isinstance(stored, dict): stored = None
            fact = extract_game(g['pgn'], side, tc_raw, clock_convention=clock_convention, opening_db=book, stored_opening=stored, opening_limit=opening_limit)
        except ValueError as exc:
            coverage['unsupported_games' if isinstance(exc, UnsupportedVariant) else 'replay_failed_games'] += 1
            if len(errors) < top_k: errors.append({'game_id': g['game_id'], 'reason': str(exc)[:200]})
            continue
        coverage['replayed_games'] += 1
        metadata['replayed_plies'] += fact['plies']; moves_games += fact['own_moves'] > 0
        standard_games += fact['standard_start']; castled += fact['castled']; queens_removed += fact['standard_start'] and fact['original_queens_removed']
        metadata['raw_rating_warnings'] += fact['raw_rating_warnings']
        metadata['starting_castling_rights_games'] += fact['initial_castling_rights']
        metadata['queen_timing_eligible_games'] += fact['standard_start'] and (fact['own_moves'] >= 5 or (fact['queen_first_move'] is not None and fact['queen_first_move'] < 6))
        metadata['games_with_material_imbalance'] += any(k != 0 and v > 0 for k, v in fact['imbalances'].items())
        metadata['last_five_eligible_moves'] += min(5, fact['own_moves'])
        timing_eligible[tc_key] += fact['own_moves']
        for point in ('10', '20', 'last_recorded'):
            checkpoint_eligible[point] += fact['own_moves'] >= (int(point) if point != 'last_recorded' else 1)
        counts.update(fact['counts']); phase_counts.update(fact['phases']); captured.update(fact['captures']); imbalances.update(fact['imbalances'])
        clock_counts.update(fact['clocks']); metadata['tc_conflicts'] += fact['tc_conflict']; metadata['inference_supported_games'] += fact['inference_supported']
        if fact['development_at_ten'] is not None: development.append(fact['development_at_ten'])
        if fact['queen_first_move'] is not None: queen_times.append(fact['queen_first_move'])
        if fact['pawn_signature']: structures[fact['pawn_signature']] += 1
        if book is not None:
            label = fact['opening']; key = (side, label['name'], label['variation'] or '', label['confidence'], label['eco'] or '')
            header_eco = label['reported_eco']
            if header_eco and re.fullmatch(r'[A-E][0-9]{2}', header_eco):
                reported_ecos[header_eco] += 1
                metadata['eco_header_disagreements'] += bool(label['eco']) and header_eco != label['eco']
            elif header_eco: metadata['invalid_eco_headers'] += 1
            if len(opening_examples[key]) < 5: opening_examples[key].append(g['game_id'])
            opening_groups[key][result] += 1; opening_sources[label['source']] += 1; opening_confidence[label['confidence']] += 1
            if label['name'] != 'Unknown': known_openings[(side, label['name'])] += 1
            if month: opening_trends[(month, side, label['name'])][result] += 1
            move_orders[(side, *fact['move_order'])] += 1
        duration_count = 0
        for kind, values in fact['durations'].items():
            if values: timing_games[kind].add(g['game_id']); timing_games['tc:'+tc_key].add(g['game_id'])
            for value in values:
                duration_groups[kind].add(value); time_by_control[tc_key].add(value); duration_count += 1
        if duration_count: durations_games.add(g['game_id'])
        for phase, values in fact['phase_durations'].items():
            if values: timing_games['phase:'+phase].add(g['game_id'])
            for value in values: phase_durations[phase].add(value)
        for value in fact['last_five']: last_five.add(value)
        for point, value in fact['checkpoints'].items(): checkpoints[point].add(value)
    partial = replay and coverage['replayed_games'] != n
    coverage.update({'metadata': dict(metadata), 'clock_moves': clock_counts['clock_moves'] if replay else None,
                     'emt_moves': clock_counts['emt_moves'] if replay else None,
                     'clock_diagnostics': dict(clock_counts) if replay else None, 'errors': errors})
    pgames = coverage['replayed_games']; pmoves = counts['moves']; total_duration = sum(x.n for x in duration_groups.values())
    duration_heuristic = duration_groups['clock_difference'].n > 0
    position_data = position_section(conn, cte, params, n, top_k) if section in ('all', 'repertoire', 'coverage') else None
    coverage['position_index'] = position_data['coverage'] if position_data is not None else {'status': 'NOT_REQUESTED'}
    def rating_summary(values):
        ordered = sorted(values)
        return {'provenance': 'OBSERVED', 'method': 'recorded_pre_game_rating_lower_rank_quartiles_v1', 'preliminary': len(values) < 20, 'observations': len(values), 'min': min(values), 'max': max(values), 'median': statistics.median(values),
                'q1': ordered[(len(ordered)-1)//4], 'q3': ordered[3*(len(ordered)-1)//4], 'kind': 'reported_pre_game_rating'}
    overview = {'outcomes': outcomes(totals), 'by_color': {c: outcomes(v) for c, v in by_color.items()},
                'ratings_by_time_control': [{'time_control': k, **rating_summary(v)} for k, v in sorted(ratings.items())[:top_k]],
                'rating_strata_count': len(ratings), 'time_controls': [{'time_control': k, **outcomes(v)} for k, v in sorted(controls.items())[:top_k]],
                'time_control_strata_count': len(controls), 'opponent_rating_bands': [{'band': k, **outcomes(v)} for k, v in sorted(opponent_bands.items())]}
    recent_counts = Counter(item[2] for item in recent)
    trends = {'monthly': [{'month': k, **outcomes(v)} for k, v in sorted(monthly.items())[-top_k:]], 'monthly_bucket_count': len(monthly),
              'rating_monthly': [{'time_control': k[0], 'month': k[1], **rating_summary(v)} for k, v in sorted(rating_series.items())[-top_k:]],
              'recent_20_dated_games': outcomes(recent_counts), 'recent_calendar': {'from': recent_from.isoformat(), 'through': day.isoformat(), 'timezone': 'Asia/Kolkata', **outcomes(recent_calendar)},
              'limitations': ['Ratings are pre-game reports, not inferred post-game ratings. Same-day order without timestamps is unknown; ID tie-breaks are not chronological evidence.', 'Missing played dates are never replaced by import timestamps.']}
    ng = sum(known_openings.values()); entropy = -sum(v/ng*math.log(v/ng) for v in known_openings.values()) if ng else None
    rep = {'classification_provenance': 'HEURISTIC', 'confidence_is_classifier_label_not_probability': True, 'openings': [{'color': k[0], 'name': k[1], 'variation': k[2] or None, 'confidence': k[3], 'eco': k[4] or None, 'evidence_game_ids': opening_examples[k], 'examples_are_bounded': True, **outcomes(v),
                         'frequency_by_color': rate(sum(v.values()), sum(sum(b.values()) for a, b in opening_groups.items() if a[0] == k[0]), games=pgames, partial=partial)}
                        for k, v in sorted(opening_groups.items(), key=lambda x: (-sum(x[1].values()), x[0]))[:top_k]],
           'reported_eco_headers': {'provenance': 'OBSERVED', 'counts': dict(reported_ecos.most_common(top_k)), 'limitation': 'Reported headers are not verified full-variation labels.'},
           'classification_sources': dict(opening_sources), 'confidence_counts': dict(opening_confidence),
           'recognized_games': ng, 'opening_bucket_count': len(opening_groups),
           'diversity': {'recognized_categories': metric(len(known_openings), method='distinct_color_opening_labels_v1', games=ng, eligible=pgames, observed=ng),
                         'entropy': metric(entropy, 'nats', 'recognized_color_opening_entropy_v1', heuristic=True, games=ng, eligible=pgames, observed=ng)},
           'move_orders': [{'color': k[0], 'start_key': k[1], 'uci_prefix': list(k[2]), 'short_game': k[3], 'distinct_games': v} for k, v in move_orders.most_common(top_k)],
           'opening_trends': [{'month': k[0], 'color': k[1], 'name': k[2], **outcomes(v)} for k, v in sorted(opening_trends.items())[-top_k:]]}
    tend = {'played_moves': metric(pmoves, games=pgames, moves=pmoves, eligible=n, observed=pgames, partial=partial),
            'event_counts': {k: v for k, v in counts.items() if k != 'repeated_piece_moves'},
            'repeated_piece_opening_moves': metric(counts['repeated_piece_moves'] if standard_games else None, method='tracked_piece_reuse_in_first20plies_v1', heuristic=True, games=standard_games, eligible=n, observed=standard_games, partial=partial),
            'event_counts_provenance': 'OBSERVED', 'event_frequencies': {k: rate(counts[k], pmoves, games=moves_games, partial=partial) for k in ('pawn_moves', 'captures', 'checks', 'promotions')},
            'castled_game_fraction': rate(castled, moves_games, games=moves_games, partial=partial),
            'both_original_queens_captured_game_fraction': rate(queens_removed, standard_games, games=standard_games, partial=partial),
            'starting_castling_rights_games': metadata['starting_castling_rights_games'], 'captured_piece_types': dict(captured),
            'phase_distribution': {k: rate(v, pmoves, games=pgames, heuristic=True, method='relative_20plies_or_total_material26_phase_v1', partial=partial) for k, v in phase_counts.items()},
            'development_at_player_move10': metric(statistics.mean(development) if development else None, 'pieces', 'original_minor_departure_by_move10_v1', heuristic=True, games=len(development), eligible=standard_games, observed=len(development)),
            'first_original_queen_move': metric(statistics.mean(queen_times) if queen_times else None, 'player_move_ordinal', 'first_original_queen_move_censored_v1', heuristic=True, games=len(queen_times), eligible=standard_games, observed=len(queen_times), limitations=['Unmoved queens and games ending early are censored; this is not a quality judgment.']),
            'queen_before_move6': rate(sum(v < 6 for v in queen_times), metadata['queen_timing_eligible_games'], games=metadata['queen_timing_eligible_games'], heuristic=True, method='queen_departure_before_move6_v1', partial=partial),
            'any_weighted_material_imbalance_game_fraction': rate(metadata['games_with_material_imbalance'], moves_games, games=moves_games, heuristic=True, method='any_nonzero_weighted_material_before_own_move_v1', partial=partial),
            'weighted_material_imbalance_before_player_moves': {'provenance': 'HEURISTIC', 'weights': {'pawn': 1, 'knight': 3, 'bishop': 3, 'rook': 5, 'queen': 9}, 'histogram': dict(sorted(imbalances.items())), 'sample_moves': pmoves},
            'pawn_placement_at_player_move10': {'provenance': 'HEURISTIC', 'method': 'exact_black_white_pawn_bitboards_v1', 'eligible_games': len(development), 'observed_games': sum(structures.values()), 'items': [{'signature': k, 'games': v} for k, v in structures.most_common(top_k)]},
            'mean_recorded_game_length_plies': metric(metadata['replayed_plies']/pgames if pgames else None, 'plies', 'replayed_mainline_length_v1', games=pgames, eligible=n, observed=pgames, partial=partial)}
    timing = {'clock_convention': clock_convention, 'derived_timing_requires_explicit_convention': True,
              'duration_sources': {k: v.report(len(timing_games[k]), pmoves, heuristic=k == 'clock_difference', method=k+'_v1', partial=partial) for k, v in duration_groups.items()},
              'usable_duration_moves': total_duration, 'games_with_durations': len(durations_games),
              'by_phase': {k: v.report(len(timing_games['phase:'+k]), phase_counts[k], heuristic=True, method='elapsed_with_heuristic_phase_v1', partial=partial) for k, v in phase_durations.items()},
              'by_time_control': [{'time_control': k, **v.report(len(timing_games['tc:'+k]), timing_eligible[k], heuristic=duration_heuristic, method='elapsed_by_raw_time_control_v1', partial=partial)} for k, v in sorted(time_by_control.items())[:top_k]],
              'remaining_clock_checkpoints': {k: v.report(v.n, checkpoint_eligible[k], method='reported_clock_checkpoint_v1', partial=partial) for k, v in checkpoints.items()},
              'last_five_recorded_player_moves': last_five.report(len(durations_games), metadata['last_five_eligible_moves'], heuristic=duration_heuristic, method='last_five_recorded_own_moves_v1', partial=partial),
              'very_fast_move_fraction': rate(sum(sum(v.bins[str(b)] for b in (1, 2)) for v in duration_groups.values()), total_duration,
                                              games=len(durations_games), heuristic=True, method='elapsed_at_most_2_seconds_v1', partial=partial),
              'limitations': ['Unverified clocks, resets/stages/delay/hourglass/daily controls do not produce inferred durations.', 'No move quality or time-pressure conclusion follows from speed. Mixed reported/derived aggregates are marked heuristic.']}
    analyses = query('SELECT count(*) FROM selected g JOIN analyses a USING(game_id)').fetchone()[0]
    return {'schema_version': '1.0', 'generated_at': datetime.now(timezone.utc).isoformat(), 'selector': selector,
            'completeness': {'metadata_complete': True, 'replay_complete': not partial if replay else None, 'requested_section': section,
                             'eligible_games': n, 'processed_games': coverage['processed_games'], 'budget_exhausted': budget_exhausted},
            'coverage': coverage, 'overview': overview if section in ('all', 'overview') else None,
            'trends': trends if section in ('all', 'trends') else None, 'repertoire': rep if section in ('all', 'repertoire') else None,
            'move_tendencies': tend if section in ('all', 'tendencies') else None,
            'time_management': timing if section in ('all', 'time-management') else None,
            'positions': position_data,
            'engine_analysis_availability': {'games_with_stored_analysis': analyses, 'not_used_by_fast_profile': True},
            'methodology_versions': {'metrics': '1', 'position_identity': '1', 'opening_book': book_checksum},
            'limitations': ['A source-scoped label is not a verified real-world identity; accounts are never automatically linked.',
                           'Metadata ratings may reflect the existing importer\'s permissive normalization; they are recorded values, not a verified rating history.',
                           'Top-K lists and monthly series are bounded; bucket counts disclose omissions. Aggregates cover all eligible games.',
                           'Board observations and named heuristic rules do not establish mistakes or player personality.',
                           'Position sections depend on explicit index coverage. No database writes, repairs, engine searches or LLM calls occur.']}
