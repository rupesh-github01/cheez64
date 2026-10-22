"""Strict Segment 16 contracts. No engine execution or game-analysis adapters."""
import hashlib
import json
from typing import Literal

import chess
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from .positions import canonical_position


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)


class CacheLimits(StrictModel):
    max_records: StrictInt = Field(50000, ge=1, le=100000000)
    max_contexts: StrictInt = Field(100000, ge=1, le=100000000)
    max_bytes: StrictInt = Field(256*1024**2, ge=8*1024**2, le=1024**4)
    reserve_bytes: StrictInt = Field(4*1024**2, ge=4*1024**2, le=64*1024**2)
    per_config: StrictInt = Field(4, ge=1, le=32)
    configs_per_context: StrictInt = Field(16, ge=1, le=128)
    per_context: StrictInt = Field(32, ge=1, le=256)
    max_misses: StrictInt = Field(10000, ge=1, le=1000000)
    retention_days: StrictInt = Field(90, ge=1, le=36500)
    cloud_fresh_days: StrictInt = Field(30, ge=1, le=36500)
    min_interval: float = Field(5, ge=5, le=3600, allow_inf_nan=False)
    per_minute: StrictInt = Field(12, ge=1, le=12)
    per_hour: StrictInt = Field(60, ge=1, le=60)

    @model_validator(mode='after')
    def consistent(self):
        if self.reserve_bytes >= self.max_bytes or self.per_config > self.per_context:
            raise ValueError('Inconsistent capacity limits')
        return self


class PositionContext(StrictModel):
    fen: str = Field(min_length=15, max_length=200)
    variant: Literal['standard'] = 'standard'
    root_fen: str | None = Field(None, max_length=200)
    moves: list[str] = Field(default_factory=list, max_length=1000)
    root_history_complete: StrictBool = False
    draw_policy: Literal['fide_claims_and_automatic_v1'] = 'fide_claims_and_automatic_v1'

    @model_validator(mode='after')
    def validate_context(self):
        self.identity()
        return self

    def identity(self, provider=False):
        fields = self.fen.split()
        if len(fields) != 6 or not fields[4].isascii() or not fields[4].isdigit() or not fields[5].isascii() or not fields[5].isdigit() or int(fields[5]) < 1:
            raise ValueError('Evaluation requires six-field FEN with valid counters')
        board = chess.Board(self.fen)
        position = canonical_position(board, self.variant)
        if board.halfmove_clock > 1000000 or board.fullmove_number > 1000000:
            raise ValueError('FEN counter out of range')
        root = None
        if self.root_fen is not None:
            if len(self.root_fen.split()) != 6:
                raise ValueError('Root requires six-field FEN')
            replay = chess.Board(self.root_fen)
            canonical_position(replay)
            root = replay.fen(en_passant='legal')
            if self.root_history_complete and root != chess.STARTING_FEN:
                raise ValueError('Only standard initial root has verified preceding history')
            for move in self.moves:
                if len(move) > 5:
                    raise ValueError('Invalid history UCI')
                parsed = replay.parse_uci(move)
                if not parsed:
                    raise ValueError('Null history move')
                replay.push(parsed)
            if replay.fen(en_passant='legal') != board.fen(en_passant='legal'):
                raise ValueError('History does not reach requested FEN')
        elif self.moves or self.root_history_complete:
            raise ValueError('History requires a root')
        value = {'version': 1, 'position_version': 1, 'variant': self.variant,
                 'position': position, 'halfmove': board.halfmove_clock,
                 'kind': 'provider_unknown' if provider else ('history' if root else 'standalone'),
                 'root': None if provider else root,
                 'moves': [] if provider else self.moves,
                 'history_complete': False if provider else self.root_history_complete,
                 'draw_policy': self.draw_policy}
        if len(canonical_json(value).encode()) > 65536:
            raise ValueError('Context exceeds 64 KiB')
        return value

    def normalized_fen(self):
        return chess.Board(self.fen).fen(en_passant='legal')


class Source(StrictModel):
    kind: Literal['local_stockfish', 'lichess_cloud']
    adapter_version: Literal[1] = 1
    engine_name: str | None = Field(None, max_length=100)
    engine_version: str | None = Field(None, max_length=200)
    engine_build: str | None = Field(None, max_length=200)
    nnue: str | None = Field(None, max_length=200)
    provenance: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def trustworthy(self):
        if self.kind == 'local_stockfish' and not all((self.engine_name, self.engine_version, self.engine_build, self.nnue)):
            raise ValueError('Local ingestion requires engine name/version/build/NNUE identity')
        if self.kind == 'lichess_cloud' and any((self.engine_name, self.engine_version, self.engine_build, self.nnue)):
            raise ValueError('Cloud engine identity is not provided by this contract')
        return self


class SearchConfig(StrictModel):
    source: Source
    requested_options: dict = Field(default_factory=dict)
    applied_options: dict | None = None
    applied_options_complete: StrictBool = False
    limit_kind: Literal['depth', 'time', 'combined', 'cloud']
    depth: StrictInt | None = Field(None, ge=1, le=512)
    time_seconds: float | None = Field(None, gt=0, le=86400, allow_inf_nan=False)
    multipv: StrictInt = Field(1, ge=1, le=5)
    perspective: Literal['white'] = 'white'
    draw_policy: Literal['fide_claims_and_automatic_v1'] = 'fide_claims_and_automatic_v1'

    @model_validator(mode='after')
    def settings(self):
        if len(canonical_json(self.model_dump()).encode()) > 8192:
            raise ValueError('Configuration exceeds 8 KiB')
        if self.source.kind == 'lichess_cloud':
            if self.limit_kind != 'cloud' or self.applied_options is not None or self.applied_options_complete or self.requested_options or self.depth is not None or self.time_seconds is not None:
                raise ValueError('Do not invent cloud search settings')
        else:
            if self.applied_options is None or not self.applied_options_complete or self.limit_kind == 'cloud':
                raise ValueError('Verified complete local settings required')
            if not self.requested_options.keys() <= self.applied_options.keys():
                raise ValueError('Applied settings must include all requested options')
            for name in ('Threads', 'Hash'):
                value = self.applied_options.get(name)
                if type(value) is not int or not 1 <= value <= (1024 if name == 'Threads' else 1048576):
                    raise ValueError('Applied Threads/Hash required and bounded')
            if (self.limit_kind in ('depth', 'combined')) != (self.depth is not None):
                raise ValueError('Depth/limit mismatch')
            if (self.limit_kind in ('time', 'combined')) != (self.time_seconds is not None):
                raise ValueError('Time/limit mismatch')
        return self

    def family(self):
        value = self.model_dump()
        if self.limit_kind == 'depth':
            value['depth'] = None
        return digest(value)


class PV(StrictModel):
    kind: Literal['cp', 'mate', 'terminal_checkmate']
    cp: StrictInt | None = Field(None, ge=-1000000, le=1000000)
    mate: StrictInt | None = Field(None, ge=-10000, le=10000)
    winner: Literal['white', 'black'] | None = None
    moves: list[str] = Field(default_factory=list, max_length=64)
    original_moves: str | None = Field(None, max_length=512)

    @model_validator(mode='after')
    def score(self):
        if self.kind == 'cp' and (self.cp is None or self.mate is not None or self.winner is not None):
            raise ValueError('Invalid cp score')
        if self.kind == 'mate' and (self.mate is None or self.mate == 0 or self.cp is not None or self.winner is not None):
            raise ValueError('Invalid mate score')
        if self.kind == 'terminal_checkmate' and (self.cp is not None or self.mate != 0 or self.winner is None or self.moves):
            raise ValueError('Invalid terminal score')
        return self


class Evaluation(StrictModel):
    context: PositionContext
    config: SearchConfig
    achieved_depth: StrictInt = Field(ge=0, le=512)
    nodes: StrictInt | None = Field(None, ge=0, le=2**63-1)
    knodes: StrictInt | None = Field(None, ge=0, le=(2**63-1)//1000)
    elapsed_seconds: float | None = Field(None, ge=0, le=86400, allow_inf_nan=False)
    pvs: list[PV] = Field(min_length=1, max_length=5)
    provider_fen: str | None = Field(None, max_length=200)

    @model_validator(mode='after')
    def legal_lines(self):
        if len(self.pvs) > self.config.multipv:
            raise ValueError('More PVs than requested')
        roots = set()
        for line in self.pvs:
            board = chess.Board(self.context.fen)
            if line.kind == 'terminal_checkmate':
                winner = 'black' if board.turn else 'white'
                if not board.is_checkmate() or line.winner != winner:
                    raise ValueError('Unproven terminal mate')
            elif not line.moves:
                raise ValueError('Nonterminal PV needs moves')
            for move in line.moves:
                parsed = board.parse_uci(move)
                if not parsed or parsed.uci() != move:
                    raise ValueError('PV must contain normalized legal orthodox UCI')
                board.push(parsed)
            root = line.moves[0] if line.moves else ''
            if root in roots:
                raise ValueError('Duplicate PV root')
            roots.add(root)
        if self.config.source.kind == 'lichess_cloud':
            if self.provider_fen is None or canonical_position(self.provider_fen) != canonical_position(self.context.fen):
                raise ValueError('Missing or incompatible provider FEN')
        elif self.provider_fen is not None:
            raise ValueError('Unexpected provider FEN')
        if len(canonical_json(self.model_dump()).encode()) > 131072:
            raise ValueError('Evaluation exceeds 128 KiB')
        return self


class Lookup(StrictModel):
    context: PositionContext
    require_trusted: StrictBool = False
    allow_advisory: StrictBool = False
    allow_partial: StrictBool = False
    allow_stale: StrictBool = False
    min_depth: StrictInt = Field(0, ge=0, le=512)
    min_pvs: StrictInt = Field(1, ge=1, le=5)
    config_hash: str | None = Field(None, pattern=r'^[a-f0-9]{64}$')
    family_hash: str | None = Field(None, pattern=r'^[a-f0-9]{64}$')
    source: Literal['local_stockfish', 'lichess_cloud'] | None = None
    allow_cloud: StrictBool = False
    cloud_multipv: StrictInt = Field(1, ge=1, le=5)
    prefer_cloud_pvs: StrictBool = False

    @model_validator(mode='after')
    def compatible_request(self):
        if self.prefer_cloud_pvs and (not self.allow_cloud or not self.allow_advisory or self.require_trusted or self.source != 'lichess_cloud'):
            raise ValueError('PV preference requires explicit advisory cloud-only opt-in')
        if self.require_trusted and not self.context.root_history_complete:
            raise ValueError('Trusted request requires complete verified history')
        return self
