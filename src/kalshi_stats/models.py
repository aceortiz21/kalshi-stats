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
    plus_10c_rate: float | None
    plus_15c_rate: float | None
    plus_20c_rate: float | None
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
