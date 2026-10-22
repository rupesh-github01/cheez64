"""Independent reasoning contracts; no change to existing cache/source meanings."""
from typing import Literal
import time
import chess
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator
from .evaluation_models import PositionContext, PV, canonical_json, digest
from .understanding_models import BudgetExceeded


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Budget:
    def __init__(self, seconds=3, max_pushes=2048, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.seconds = seconds
        self.total_seconds = seconds
        self.max_pushes = max_pushes
        self.pushes = 0

    def check(self, **kwargs):
        if self.clock()-self.started >= self.seconds:
            raise BudgetExceeded('REASONING_TIME_BUDGET')

    def push(self, **kwargs):
        self.check()
        if self.pushes >= self.max_pushes:
            raise BudgetExceeded('REASONING_PUSH_BUDGET')
        self.pushes += 1


def replay_context(context, budget):
    board = chess.Board(context.root_fen or context.fen)
    for uci in context.moves:
        budget.check()
        if board.outcome(claim_draw=False):
            raise ValueError('HISTORY_CONTINUES_AFTER_TERMINATION')
        move = board.parse_uci(uci)
        if not move or move.uci() != uci:
            raise ValueError('NONCANONICAL_HISTORY')
        board.push(move)
    if board.fen(en_passant='legal') != chess.Board(context.fen).fen(en_passant='legal'):
        raise ValueError('CONTEXT_MISMATCH')
    return board


class Engine(Contract):
    family: Literal['stockfish']
    uci_name: str = Field(min_length=1, max_length=100)
    version: str = Field(min_length=1, max_length=200)
    binary_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    build: str = Field(pattern=r'^sha256:[a-f0-9]{64}$')
    build_method: Literal['binary_sha256', 'pcis_command_and_files_sha256']
    nnue: str = Field(pattern=r'^(embedded:)?sha256:[a-f0-9]{64}$')
    identity_method: Literal['exported_uci_inventory_and_streamed_hashes']

    @model_validator(mode='after')
    def consistent(self):
        if self.build_method == 'binary_sha256' and self.build != 'sha256:'+self.binary_sha256:
            raise ValueError('BUILD_HASH_MISMATCH')
        if self.nnue.startswith('embedded:') and self.nnue != 'embedded:'+self.build:
            raise ValueError('EMBEDDED_NNUE_MISMATCH')
        return self


class Settings(Contract):
    engine: Engine
    requested_options: dict = Field(max_length=64)
    applied_options: dict = Field(min_length=2, max_length=64)
    option_inventory: list[str] = Field(min_length=2, max_length=64)
    applied_options_complete: StrictBool
    application_method: Literal['exporter_readyok_receipt']
    reset_policy: Literal['ucinewgame_clear_hash_per_context']
    limit_kind: Literal['depth', 'time', 'combined', 'nodes']
    requested_depth: StrictInt | None = Field(default=None, ge=1, le=512)
    time_seconds: float | None = Field(default=None, gt=0, le=86400, allow_inf_nan=False)
    node_limit: StrictInt | None = Field(default=None, ge=1, le=2**63-1)
    multipv: StrictInt = Field(ge=1, le=5)
    search_scope: Literal['unrestricted', 'restricted']
    searched_roots: list[str] = Field(default_factory=list, max_length=256)
    perspective: Literal['white']
    draw_policy: Literal['fide_claims_and_automatic_v1']

    @model_validator(mode='after')
    def complete(self):
        if self.applied_options_complete is not True:
            raise ValueError('COMPLETE_APPLIED_OPTIONS_REQUIRED')
        if len(set(self.option_inventory)) != len(self.option_inventory) or set(self.option_inventory) != set(self.applied_options):
            raise ValueError('INCOMPLETE_OPTION_INVENTORY')
        if not self.requested_options.keys() <= self.applied_options.keys():
            raise ValueError('MISSING_REQUESTED_OPTIONS')
        if any(self.applied_options[k] != v for k, v in self.requested_options.items()):
            raise ValueError('APPLIED_OPTION_MISMATCH')
        for name, value in self.applied_options.items():
            if (not isinstance(name, str) or not 1 <= len(name) <= 100 or any(c in name for c in '\n\r\0') or
                type(value) not in (str, int, bool) or len(str(value)) > 200 or any(c in str(value) for c in '\n\r\0')):
                raise ValueError('INVALID_UCI_OPTION')
        for name, maximum in (('Threads', 1024), ('Hash', 1048576)):
            if type(self.applied_options.get(name)) is not int or not 1 <= self.applied_options[name] <= maximum:
                raise ValueError('THREADS_HASH_REQUIRED')
        if self.applied_options.get('MultiPV') != self.multipv:
            raise ValueError('MULTIPV_OPTION_MISMATCH')
        if self.applied_options.get('UCI_Chess960', False) is not False or self.applied_options.get('Ponder', False) is not False:
            raise ValueError('STANDARD_NONPONDER_SEARCH_REQUIRED')
        if (self.limit_kind in ('depth', 'combined')) != (self.requested_depth is not None):
            raise ValueError('DEPTH_LIMIT_MISMATCH')
        if (self.limit_kind in ('time', 'combined')) != (self.time_seconds is not None):
            raise ValueError('TIME_LIMIT_MISMATCH')
        if (self.limit_kind == 'nodes') != (self.node_limit is not None):
            raise ValueError('NODE_LIMIT_MISMATCH')
        if (self.search_scope == 'restricted') != bool(self.searched_roots):
            raise ValueError('SEARCH_SCOPE_MISMATCH')
        if len(canonical_json(self.model_dump()).encode()) > 8192:
            raise ValueError('CONFIGURATION_TOO_LARGE')
        return self


class Producer(Contract):
    name: str = Field(min_length=1, max_length=100, pattern=r'^[\w .:-]+$')
    exporter_version: str = Field(min_length=1, max_length=100, pattern=r'^[\w .:-]+$')
    simulation: StrictBool
    uncertain: StrictBool
    method: Literal['external_export', 'synthetic_fixture']
    receipt: Literal['uci_inventory_readyok_exact_final_iteration']

    @model_validator(mode='after')
    def honest(self):
        if (self.method == 'synthetic_fixture') != self.simulation:
            raise ValueError('SIMULATION_DECLARATION_CONFLICT')
        return self


class Line(Contract):
    rank: StrictInt = Field(ge=1, le=5)
    depth: StrictInt = Field(ge=0, le=512)
    iteration: StrictInt = Field(ge=1, le=1000000)
    bound: Literal['exact', 'lower', 'upper']
    score: PV


class Record(Contract):
    id: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_.:-]+$')
    context: PositionContext
    settings: Settings
    cohort: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_.:-]+$')
    session: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_.:-]+$')
    edge_uci: str | None = Field(default=None, pattern=r'^[a-h][1-8][a-h][1-8][qrbn]?$')
    achieved_depth: StrictInt = Field(ge=0, le=512)
    final_iteration: StrictInt = Field(ge=1, le=1000000)
    nodes: StrictInt | None = Field(default=None, ge=0, le=2**63-1)
    elapsed_seconds: float | None = Field(default=None, ge=0, le=86400, allow_inf_nan=False)
    score_conversion: Literal['white_relative_export', 'side_to_move_converted_to_white_by_exporter']
    lines: list[Line] = Field(min_length=1, max_length=5)

    @model_validator(mode='after')
    def iteration_receipt(self):
        ranks = [x.rank for x in self.lines]
        if ranks != list(range(1, len(ranks)+1)) or len(self.lines) > self.settings.multipv:
            raise ValueError('INVALID_MULTIPV_RANKS')
        if any(x.depth != self.achieved_depth or x.iteration != self.final_iteration for x in self.lines):
            raise ValueError('MIXED_FINAL_ITERATION')
        if len({x.score.moves[0] if x.score.moves else '' for x in self.lines}) != len(self.lines):
            raise ValueError('DUPLICATE_PV_ROOT')
        return self


class Bundle(Contract):
    schema_version: Literal['reasoning-evidence/1']
    policy_version: Literal[1]
    root: PositionContext
    producer: Producer
    mode: Literal['ROOT_MULTIPV_COHORT', 'SIBLING_CHILD_COHORT']
    records: list[Record] = Field(min_length=1, max_length=5)

    @model_validator(mode='after')
    def unique(self):
        if len({x.id for x in self.records}) != len(self.records):
            raise ValueError('DUPLICATE_RECORD_ID')
        return self


class Acceptance(Contract):
    origin: Literal['EXTERNAL_ATTESTED', 'SYNTHETIC_TEST']
    acknowledgement: Literal['operator_reviewed_export_not_cryptographic_proof', 'synthetic_contract_only']
    accepted_at: float = Field(ge=0, allow_inf_nan=False)
    payload_hash: str = Field(pattern=r'^[a-f0-9]{64}$')


class Envelope(Contract):
    schema_version: Literal['reasoning-envelope/1']
    bundle: Bundle
    acceptance: Acceptance

    @model_validator(mode='after')
    def bind_acceptance(self):
        if digest(self.bundle.model_dump()) != self.acceptance.payload_hash:
            raise ValueError('PAYLOAD_DIGEST_MISMATCH')
        synthetic = self.bundle.producer.simulation
        if (self.acceptance.origin == 'SYNTHETIC_TEST') != synthetic:
            raise ValueError('ORIGIN_SIMULATION_CONFLICT')
        expected = 'synthetic_contract_only' if synthetic else 'operator_reviewed_export_not_cryptographic_proof'
        if self.acceptance.acknowledgement != expected or self.bundle.producer.uncertain:
            raise ValueError('UNACCEPTABLE_RECEIPT')
        return self


def validate_bundle(bundle, budget=None):
    """All complete PVs, history termination and parent-child bindings before use."""
    budget = budget or Budget()
    root = replay_context(bundle.root, budget)
    seen_edges = set()
    if sum(r.edge_uci is None for r in bundle.records) > 1:
        raise ValueError('MULTIPLE_ROOT_RECORDS')
    for record in bundle.records:
        budget.check()
        board = replay_context(record.context, budget)
        if record.edge_uci is None:
            if record.context.identity() != bundle.root.identity():
                raise ValueError('ROOT_RECORD_CONTEXT_MISMATCH')
        else:
            if record.edge_uci in seen_edges:
                raise ValueError('DUPLICATE_CHILD_EDGE')
            seen_edges.add(record.edge_uci)
            child = root.copy(stack=True)
            if child.outcome(claim_draw=False):
                raise ValueError('CHILD_OF_TERMINAL_ROOT')
            budget.push()
            child.push_uci(record.edge_uci)
            expected_moves = bundle.root.moves+[record.edge_uci]
            if (record.context.root_fen != bundle.root.root_fen or record.context.moves != expected_moves or
                record.context.root_history_complete != bundle.root.root_history_complete or
                record.context.fen != child.fen(en_passant='legal')):
                raise ValueError('CHILD_HISTORY_MISMATCH')
        if record.settings.draw_policy != record.context.draw_policy:
            raise ValueError('DRAW_POLICY_MISMATCH')
        restricted = set(record.settings.searched_roots)
        if any(not uci or not board.is_legal(chess.Move.from_uci(uci)) for uci in restricted):
            raise ValueError('ILLEGAL_SEARCH_RESTRICTION')
        for line in record.lines:
            pv = line.score
            if pv.original_moves is not None:
                raise ValueError('PROVIDER_NOTATION_NOT_ALLOWED')
            replay = board.copy(stack=True)
            if pv.kind == 'terminal_checkmate':
                winner = 'black' if replay.turn else 'white'
                if not replay.is_checkmate() or pv.winner != winner:
                    raise ValueError('UNPROVEN_TERMINAL_MATE')
            elif not pv.moves:
                raise ValueError('EMPTY_NONTERMINAL_PV')
            if restricted and pv.moves and pv.moves[0] not in restricted:
                raise ValueError('PV_OUTSIDE_RESTRICTION')
            for uci in pv.moves:
                budget.push()
                if replay.outcome(claim_draw=False):
                    raise ValueError('PV_CONTINUES_AFTER_TERMINATION')
                move = replay.parse_uci(uci)
                if not move or move.uci() != uci:
                    raise ValueError('ILLEGAL_OR_NONCANONICAL_PV')
                replay.push(move)
            if replay.is_checkmate() and pv.kind != 'terminal_checkmate':
                winning_white = not replay.turn
                if pv.kind != 'mate' or (pv.mate > 0) != winning_white:
                    raise ValueError('TERMINAL_LINE_SCORE_TYPE_OR_PERSPECTIVE_MISMATCH')
            if len(record.context.moves)+len(pv.moves) > 1000:
                raise ValueError('CONTINUATION_HISTORY_CAP')
    return bundle


class ReasoningResponse(Contract):
    schema_version: Literal['position-reasoning/1'] = 'position-reasoning/1'
    policy_version: Literal[1] = 1
    template_version: Literal[1] = 1
    generated_at: str
    status: Literal['AVAILABLE', 'PARTIAL', 'INSUFFICIENT_EVIDENCE', 'CONFIG_REQUIRED', 'INCOMPATIBLE', 'RESOURCE_LIMIT']
    source: dict
    root: dict
    evidence: dict
    candidates: list[dict] = Field(max_length=4)
    comparison: dict
    lines: list[dict] = Field(max_length=2)
    checkpoints: list[dict] = Field(max_length=8)
    claims: list[dict] = Field(max_length=16)
    explanations: list[dict] = Field(max_length=16)
    coverage: dict
    limitations: list[str]
