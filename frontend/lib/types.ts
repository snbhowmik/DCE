// Shapes of the run payload served by the API (backend/dce/payload.py). Only the fields the UI reads.

export type Mode = "STABILITY" | "GROWTH" | "D2C_EXPANSION";
export const MODES: Mode[] = ["STABILITY", "GROWTH", "D2C_EXPANSION"];

export type Q = { q10: number[]; q50: number[]; q90: number[] };
export type Dist = { mean: number; p10: number; p50: number; p90: number };

export interface RunRow {
  run_id: string;
  world_id: string;
  dataset_hash: string;
  mode: Mode;
  status: string;
  created_at: string;
}

export interface Levers {
  mode: string;
  mode_overrides?: Record<string, unknown> | null;
  d2c_demand_pct?: number;
  b2b_demand_pct?: number;
  account_id?: string | null;
  account_demand_pct?: number;
  from_week?: number;
  capacity_pct?: number;
  capacity_from_week?: number;
  capacity_to_week?: number;
  coman_available?: boolean;
  budget_pct?: number;
}

export interface OnboardingOption {
  start_month: number;
  ramp: "full" | "50_100" | "33_66_100";
  revenue_inr: number;
  contribution_inr: number;
  b2b_fill_existing: number;
  candidate_fill: number;
  capacity_share: number;
  p_any_b2b_shortfall: number;
  d2c_allocated_kg: number;
  delta: Record<string, number>;
  acceptable: boolean;
  issues: string[];
}

export interface OnboardingResult {
  world_id: string;
  mode: string;
  base: Record<string, number>;
  options: OnboardingOption[];
  recommendation: { class: "accept_now" | "accept_from" | "phase" | "decline"; reasons: string[]; start_month: number; ramp: string };
  run_id: string;
  seconds: number;
}

export interface ScenarioResult {
  run_id: string;
  parent_run_id: string | null;
  world_id: string;
  mode: string;
  changes: string[];
  seconds: number;
}

export interface DatasetRow {
  dataset_hash: string;
  world_id: string;
  name: string | null;
  contract_version: string | null;
  n_errors: number;
  n_warnings: number;
  history: { first_week?: string; last_week?: string; n_weeks?: string | number } | null;
  runs: RunRow[];
}

export interface Alert {
  kind: "breach" | "surplus";
  start: string;
  end: string;
  weeks_until: number;
  peak_probability: number;
  expected_kg: number;
}

export interface Reason {
  code: string;
  detail: string;
  value: number | null;
}

export interface AllocationRow {
  channel: "D2C" | "B2B";
  region_id: string | null;
  account_id: string | null;
  month: number;
  month_start: string;
  demand_kg: number;
  allocated_kg: number;
  unmet_kg: number;
  eligible: boolean;
  floor_slack_kg: number;
  fill_rate: number | null;
  line?: string;
  reasons?: Reason[] | null;
}

export interface Payload {
  schema_version: number;
  run: {
    run_id: string;
    world_id: string;
    dataset_hash: string;
    config_hash: string;
    git_sha: string;
    created_at: string;
    timing_s: { upstream: number; plan: number };
    mode: Mode;
    seed: number;
    history_window: [string, string];
    decision_week: string;
    horizon: string[];
    months: { idx: number; start: string; end: string; n_weeks: number }[];
    product_line: string;
    skus: string[];
    regions: string[];
    accounts: string[];
    plan_status: string;
    forecast_hash: string;
    kind?: "scenario";
    parent_run_id?: string | null;
    changes?: string[];
    levers?: Levers;
    mode_config: Record<string, unknown> & { q_capacity: number; breach_threshold: number };
  };
  kpis: {
    demand_kg: number;
    allocated_kg: number;
    unmet_kg: number;
    d2c_allocated_kg: number;
    b2b_allocated_kg: number;
    capacity_planned_kg: number;
    revenue_inr: Dist;
    contribution_inr: Dist;
    d2c_fill_rate: Dist;
    b2b_fill_rate: Dist;
    p_any_b2b_shortfall: number;
    waste_kg: Dist;
    coman_requested_kg: number;
    coman_cost_inr: number;
    spend_planned_inr: number;
    spend_recommended_inr: number;
    n_breach_alerts: number;
    n_surplus_alerts: number;
    best_baseline: string | null;
    best_baseline_revenue_inr: number | null;
    best_baseline_waste_kg: number | null;
  };
  demand: {
    forecast: { total: Q; D2C: Q; B2B: Q };
    by_region: Record<string, Q>;
    by_account: Record<string, Q>;
    history: { week_start: string; channel: "D2C" | "B2B"; demand_kg: number; sales_kg: number }[];
    out_of_scope: { channel: string; sku_id: string; demand_kg: number; first_week: string }[];
  };
  capacity: {
    inhouse: { week_start: string; h: number; planned_kg: number; mean: number; q10: number; q50: number; q90: number }[];
    supply_with_plan: { week_start: string; h: number; supply_q10: number; supply_q50: number; supply_q90: number }[];
    history: { week_start: string; output_kg: number; planned_kg: number; failed_batches: number; batches: number }[];
    partners: {
      coman_id: string;
      lead_time_weeks: number;
      min_kg_per_week: number;
      max_kg_per_week: number;
      unit_cost_inr_per_kg: number;
      min_active_weeks: number;
      reliability_mean: number;
      reliability_from_prior: boolean;
      available_from: string;
    }[];
    perishability: { shelf_life_days: number; carryover_weeks: number; waste_cost_inr_per_kg: number };
    planned_at_quantile: number;
    planned_monthly_kg: number[];
  };
  plan: {
    status: string;
    objective: number | null;
    allocation: AllocationRow[];
    spend: {
      region_id: string;
      month: number;
      planned_inr: number;
      recommended_inr: number;
      delta_inr: number;
      low_confidence: boolean;
      gated: boolean;
      res: number | null;
      spend_cap_inr: number | null;
    }[];
    coman: {
      coman_id: string;
      month: number;
      active: boolean;
      requested_kg: number;
      expected_delivered_kg: number;
      available_weeks: number;
      cost_inr: number;
    }[];
    slacks: { name: string; kind: string; amount: number; penalty_per_unit: number; description: string }[];
    binding: { constraint: string; kind: string; shadow_price: number; meaning: string }[];
    waste_kg: number[];
    carry_kg: number[];
  };
  stress: {
    n_paths: number;
    plan: string;
    summary: Record<string, Dist>;
    comparison: { plan: string; metric: string; mean: number; p10: number; p50: number; p90: number }[];
  };
  risk: {
    breach_threshold: number;
    surplus_share: number;
    surplus_probability: number;
    weekly: {
      week_start: string;
      h: number;
      demand_q10: number;
      demand_q50: number;
      demand_q90: number;
      supply_q10: number;
      supply_q50: number;
      supply_q90: number;
      p_breach: number;
      expected_shortfall_kg: number;
      p_surplus: number;
      expected_surplus_kg: number;
      breach: boolean;
      surplus: boolean;
    }[];
    alerts: Alert[];
    breach_week: string | null;
    surplus_week: string | null;
  };
  markets: Market[];
  accounts: Account[];
  health: Health;
}

export interface Funnel {
  spend_inr?: number;
  unique_visitors?: number;
  leads?: number;
  conversions?: number;
  revenue_inr?: number;
  new_customers?: number;
  nps_responses?: number;
  bounce_rate: number | null;
  cpl_inr: number | null;
  conversion_rate: number | null;
  cac_inr: number | null;
  churn_rate: number | null;
  nps: number | null;
}

export interface Market {
  region_id: string;
  res: {
    res: number;
    confidence: number;
    n_eff: number;
    lift: number | null;
    econ: number | null;
    retention: number | null;
    nps: number | null;
    z_lift: number;
    z_econ: number;
    z_retention: number;
    z_nps: number;
    missing_components: string[];
    cac_inr: number | null;
    ltv_window_inr: number | null;
    one_minus_churn: number | null;
    repeat_rate: number | null;
  } | null;
  funnel_12m: Funnel;
  funnel_all: Funnel;
  funnel_period: [string | null, string | null];
  ltv: { ltv_inr: number; n_customers: number; n_cohorts: number } | null;
  response: {
    low_confidence: boolean;
    reasons: string[];
    elasticity: number | null;
    lift_at_mean_kg: number | null;
    lift_ci: [number | null, number | null];
    r2: number | null;
    mean_spend_inr: number | null;
    spend_cap_inr: number | null;
  } | null;
  response_skipped: string | null;
  spend: Payload["plan"]["spend"];
}

export interface Account {
  account_id: string;
  region_id: string;
  status: string;
  contract_start: string | null;
  contract_end: string | null;
  committed_kg_per_month: number | null;
  effective_end: string | null;
  end_assumed_rolling: boolean;
  reason: string | null;
  included: boolean;
  in_plan: boolean;
  aqs: {
    account_type: string;
    aqs: number;
    confidence: number;
    prior: boolean;
    capacity_share: number;
    exceeds_concentration_cap: boolean;
    volume: number;
    stability: number;
    reach: number;
    margin: number;
    reliability: number;
    penalty: number;
    z_volume: number;
    z_stability: number;
    z_reach: number;
    z_margin: number;
    z_reliability: number;
    z_penalty: number;
    months_history: number;
    profile: string;
  } | null;
  allocation: { month: number; demand_kg: number; allocated_kg: number; unmet_kg: number; fill_rate: number | null }[];
}

export interface Health {
  validation: {
    ok: boolean;
    n_errors: number;
    n_warnings: number;
    contract_version: string;
    history: { first_week?: string; last_week?: string; n_weeks?: string | number } | null;
    issues: { table: string; column: string | null; rule: string; severity: string; message: string; n_rows: number }[];
  } | null;
  selection: { series_id: string; model: string; reason: string; pinball: number; mase: number | null; baseline_mase: number | null }[];
  model_counts: Record<string, number>;
  share_beating_baseline: number | null;
  coverage: { series_id: string; coverage_raw: number; n: number; coverage_calibrated: number }[];
  coverage_mean_raw: number | null;
  coverage_mean_calibrated: number | null;
  anomalies: { series_id: string; week_start: string; y: number; baseline: number; robust_z: number; y_clean: number }[];
}

export interface Brief {
  run_id: string;
  source: "llm" | "template";
  model: string | null;
  headline: string;
  findings: string[];
  actions: string[];
  facts: string[];
  fallback_reason: string | null;
  violations: { token: string; reason: string }[];
  generated_at: string;
  log?: { prompt: string; response: string }[];
  system_prompt?: string;
}

export interface CompareResult {
  world_id: string;
  dataset_hash: string;
  modes: {
    mode: Mode;
    run_id: string;
    forecast_hash: string;
    kpis: Payload["kpis"];
    stress: Record<string, Dist>;
    allocation: { channel: string; month: number; allocated_kg: number }[];
    alerts: Alert[];
    mode_config: Record<string, unknown>;
  }[];
}

export interface DecisionRow {
  id: number;
  rec_id: string;
  run_id: string | null;
  kind: string | null;
  summary: string | null;
  action: "accept" | "reject";
  reason: string;
  user: string;
  ts: string;
}
