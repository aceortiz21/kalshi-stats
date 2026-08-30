from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ScenarioDefinition:
    id: str
    name: str
    description: str
    trigger_price_min: float
    trigger_price_max: float
    targets: list[float]
    elapsed_seconds_min: int = 0
    elapsed_seconds_max: int = 900
    seconds_remaining_min: int = 0
    seconds_remaining_max: int = 900
    trade_side: str = "same"
    occurrence_mode: str = "first_per_market"
    cooldown_seconds: int = 60


@dataclass(slots=True)
class Observation:
    observed_ts: int
    seconds_remaining: int
    elapsed_seconds: int
    yes_close: float
    yes_low: float
    yes_high: float
    source: str


@dataclass(slots=True)
class ScenarioOccurrence:
    scenario_id: str
    market_ticker: str
    trigger_side: str
    traded_side: str
    trigger_ts: int
    entry_price: float
    seconds_remaining: int
    elapsed_seconds: int
    eventual_win: bool
    best_subsequent_price: float | None
    worst_subsequent_price: float | None
    max_favorable_excursion: float | None
    max_adverse_excursion: float | None
    max_favorable_excursion_pct: float | None
    max_adverse_excursion_pct: float | None
    time_to_best_price: int | None
    time_to_worst_price: int | None
    price_after_seconds: dict[int, float | None] = field(default_factory=dict)
    target_hit_seconds: dict[float, int | None] = field(default_factory=dict)
    target_profit: dict[float, float] = field(default_factory=dict)


@dataclass(slots=True)
class ScenarioSummary:
    definition: ScenarioDefinition
    occurrences: int
    path_observations: int
    unique_markets: int
    win_rate: float | None
    win_rate_ci_low: float | None
    win_rate_ci_high: float | None
    avg_entry_price: float | None
    median_entry_price: float | None
    avg_best_subsequent_price: float | None
    median_best_subsequent_price: float | None
    avg_worst_subsequent_price: float | None
    median_worst_subsequent_price: float | None
    avg_max_favorable_excursion: float | None
    median_max_favorable_excursion: float | None
    avg_max_adverse_excursion: float | None
    median_max_adverse_excursion: float | None
    avg_max_favorable_excursion_pct: float | None
    avg_max_adverse_excursion_pct: float | None
    avg_time_to_best_price: float | None
    avg_time_to_worst_price: float | None
    median_time_to_best_price: float | None
    median_time_to_worst_price: float | None
    avg_price_after_seconds: dict[int, float | None]
    target_touch_counts: dict[float, int]
    target_eligible_counts: dict[float, int]
    target_hit_rates: dict[float, float | None]
    median_time_to_targets: dict[float, float | None]
    time_breakdown: dict[str, dict[str, float | int | None]]
    low_sample_warning: bool
    overlap_market_counts: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class MatrixCell:
    price_bucket: str
    time_bucket: str
    observations: int
    path_observations: int
    unique_markets: int
    win_rate: float | None
    touch_30_rate: float | None
    touch_35_rate: float | None
    touch_40_rate: float | None
    touch_50_rate: float | None
    plus_5c_rate: float | None
    plus_5c_eligible_n: int
    plus_10c_rate: float | None
    plus_10c_eligible_n: int
    plus_15c_rate: float | None
    plus_15c_eligible_n: int
    plus_20c_rate: float | None
    plus_20c_eligible_n: int
    avg_best_subsequent_price: float | None
    median_best_subsequent_price: float | None


@dataclass(slots=True)
class LiveScenarioMatch:
    market_ticker: str
    market_status: str
    side: str
    current_price: float
    seconds_remaining: int
    scenario_id: str
    scenario_name: str
    historical_occurrences: int
    historical_win_rate: float | None
    target_hit_rates: dict[float, float]
    avg_target_profit: dict[float, float | None]


@dataclass(slots=True)
class ActiveMarketSideView:
    market_ticker: str
    market_status: str
    side: str
    current_price: float
    seconds_remaining: int
    price_bucket: str
    time_bucket: str
    observations: int
    win_rate: float | None
    plus_5c_rate: float | None
    plus_10c_rate: float | None
    plus_15c_rate: float | None
    plus_20c_rate: float | None
    touch_30_rate: float | None
    touch_35_rate: float | None
    touch_40_rate: float | None
    touch_50_rate: float | None
    avg_best_subsequent_price: float | None
    median_best_subsequent_price: float | None
    matched_scenarios: list[str]

    # Optional fast/live quote fields. Historical callers can
    # continue constructing this model without supplying them.
    bid_price: float | None = None
    ask_price: float | None = None
    close_ts: int | None = None
    quote_ts_ms: int | None = None



@dataclass(slots=True)
class ValidatedSetup:
    price_bucket: str
    time_bucket: str

    discovery_path_n: int
    discovery_plus_10c_rate: float
    discovery_baseline_rate: float
    discovery_uplift: float

    holdout_path_n: int
    holdout_plus_10c_rate: float | None
    holdout_baseline_rate: float | None
    holdout_uplift: float | None

    validation_status: str


@dataclass(slots=True, frozen=True)
class ExitStrategy:
    """A mechanical historical exit rule."""

    id: str
    name: str
    take_profit_cents: int | None = None
    stop_loss_cents: int | None = None
    time_exit_seconds: int | None = None
    hold_to_settlement: bool = True


@dataclass(slots=True)
class StrategyOutcome:
    """Result of applying one exit strategy to one historical entry."""

    strategy_id: str
    market_ticker: str
    traded_side: str
    entry_ts: int
    entry_price: float

    exit_reason: str
    exit_price: float
    profit: float
    holding_seconds: int | None

    take_profit_hit: bool
    stop_loss_hit: bool
    ambiguous: bool


@dataclass(slots=True)
class StrategySummary:
    """Aggregate historical results for one exit strategy."""

    strategy: ExitStrategy
    observations: int
    wins: int
    losses: int
    breakevens: int
    ambiguous: int

    win_rate: float | None
    avg_profit: float | None
    median_profit: float | None
    profit_stddev: float | None
    profit_ci_low: float | None
    profit_ci_high: float | None

    take_profit_rate: float | None
    stop_loss_rate: float | None
    settlement_exit_rate: float | None
    time_exit_rate: float | None
    ambiguous_rate: float | None


@dataclass(slots=True)
class StrategyEntry:
    """One deduplicated historical price/time-state entry."""

    market_ticker: str
    side: str
    entry_index: int
    entry_ts: int
    entry_price: float
    price_bucket: str
    time_bucket: str
    seconds_remaining: int
    eventual_win: bool

@dataclass(slots=True)
class ValidatedStrategyResult:
    """Discovery/holdout validation result for one price/time strategy."""

    price_bucket: str
    time_bucket: str
    strategy: ExitStrategy

    discovery_summary: StrategySummary
    holdout_summary: StrategySummary

    validation_status: str
    ambiguity_mode: str
