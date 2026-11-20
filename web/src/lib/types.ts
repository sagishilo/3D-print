// Shapes of the gateway's JSON (gateway/app/*.py) and of the CloudEvents envelopes on the live feed.

export type Tone = 'ok' | 'warn' | 'danger' | 'idle' | 'accent'

export interface Envelope<T = Record<string, unknown>> {
  id: string
  source: string
  type: string
  subject?: string
  time: string
  kind: 'event' | 'command' | 'reply'
  correlationid: string
  causationid?: string
  data: T
}

export type PrinterStatus = 'idle' | 'printing' | 'paused' | 'finished' | 'failed' | 'offline' | 'calibrating'

export interface AmsTray {
  unit: number
  slot: number
  material: string | null
  color: string | null
  remaining_pct: number | null
}

export interface AmsUnit {
  unit: number
  humidity_level: number | null
  temp: number | null
  trays: AmsTray[]
}

export interface PrinterHardware {
  chamber_temp: number | null
  fans: Record<string, number>
  speed_mode: string | null
  speed_pct: number | null
  nozzle_diameter: number | null
  nozzle_type: string | null
  wifi_dbm: number | null
  firmware: string | null
  stage: string | null
  homed_axes: string | null
  bed_mesh: string | null
  host_load: number | null
  ams: AmsUnit[]
  hms: string[]
}

export interface PrinterState {
  printer_id: string
  vendor: string
  status: PrinterStatus
  plate_id?: string | null
  progress: number
  layer?: number | null
  total_layers?: number | null
  nozzle_temp?: number | null
  nozzle_target?: number | null
  bed_temp?: number | null
  bed_target?: number | null
  remaining_s?: number | null
  error_code?: string | null
  observed_at: string
  hardware?: PrinterHardware | null
}

export interface RunningCalibration {
  calibration_id: string
  routine: string
  requested_by: string
  started_at: string
}

export interface Printer {
  id: string
  vendor: string
  protocol: string
  model: string
  simulated: boolean
  bed_mm: [number, number]
  max_z_mm?: number | null        // build height (fleet.toml / the printer model)
  ams_slots?: number              // 0: one external spool
  ams_filaments?: string[]        // what is in the AMS slots, in order ("PLA black")
  nozzle_mm: number
  material: string
  color: string
  online: boolean
  busy: boolean
  bed_clear: boolean
  updated_at: string
  state: PrinterState | null
  camera: boolean
  calibration?: RunningCalibration | null
}

export interface Routine {
  key: string
  label: string
  help: string
  max_minutes: number
  heater: 'nozzle' | 'bed' | null
  default_temp: number | null
}

export interface CalibrationRun {
  calibration_id: string
  routine: string
  temperature: number | null
  requested_by: string
  status: 'running' | 'done' | 'failed' | 'refused'
  detail: string | null
  started_at: string
  finished_at: string | null
}

export interface PrinterDetail {
  printer: Printer
  routines: Routine[]
  calibrations: CalibrationRun[]
  sessions: { plate_id: string; started_at: string; finished_at: string | null; succeeded: boolean | null }[]
  pause_checks: {
    reason: string
    requested_by: string
    confirmed: boolean
    latency_ms: number | null
    attempts: number
    final_status: string | null
    checked_at: string
  }[]
  failures: { plate_id: string | null; detector: string; severity: string; message: string; action_taken: string; detected_at: string }[]
  stats: { prints_7d: number; succeeded_7d: number; failed_7d: number; print_hours_7d: number }
}

export type Severity = 'info' | 'warning' | 'critical'

export interface LogItem {
  topic: string
  stream: string
  seq: number
  data: Envelope
  severity: Severity
  printer_id: string | null
  time: string | null
  source: string | null
}

export type TelemetrySample = Pick<
  PrinterState,
  | 'observed_at'
  | 'status'
  | 'plate_id'
  | 'progress'
  | 'layer'
  | 'total_layers'
  | 'nozzle_temp'
  | 'nozzle_target'
  | 'bed_temp'
  | 'bed_target'
  | 'remaining_s'
  | 'error_code'
>

export interface AgentRow {
  name: string
  label: string
  detail: string
  tier: 'core' | 'optional' | 'other'
  status: 'healthy' | 'late' | 'down' | 'planned'
  heartbeat_age_s: number | null
  version: string | null
  uptime_s: number | null
  host: string | null
  queue: number | null
}

export interface Overview {
  nats: {
    ok: boolean
    version: string | null
    uptime: string | null
    connections: number | null
    msgs_per_s: number | null
    mem_bytes: number | null
    jetstream: { streams: number | null; consumers: number | null; messages: number | null; bytes: number | null }
  }
  postgres: { ok: boolean; version?: string; connections?: number; max_connections?: number; slow_queries?: number }
  go2rtc: { ok: boolean; version: string | null }
  gateway: { ok: boolean; version: string; ws_clients: number; uptime_s: number }
  consumer_lag: { total: number; max_agent: string | null; max: number }
  dlq: { messages: number | null }
}

export interface NatsConnection {
  cid: number
  name: string | null
  ip: string
  port: number
  uptime: string
  subscriptions: number
  in_msgs: number
  out_msgs: number
  rtt: string | null
  lang: string | null
  version: string | null
}

export interface StreamRow {
  name: string
  subjects: string[]
  storage: string
  max_age_s: number
  messages: number
  bytes: number
  consumers: number
  first_seq: number
  last_seq: number
}

export interface DeadLetter {
  seq: number
  time: string | null
  original_subject: string | null
  original_id: string | null
  consumer: string | null
  error: string | null
  deliveries: number | null
  original: Envelope | null
}

export interface Setting {
  key: string
  label: string
  help: string
  default: unknown
  phase: number | null
  minimum?: number | null
  maximum?: number | null
  value: unknown
  is_default: boolean
  changed_by: string | null
  changed_at: string | null
  changed_value: unknown // the value that last change set (the current value may be an optimistic one)
}

export interface Approval {
  approval_id: string
  action: string
  requested_by: string
  summary: string
  details: Record<string, unknown>
  status: 'pending' | 'approved' | 'rejected'
  decided_by: string | null
  note: string | null
  created_at: string
  decided_at: string | null
}

export interface FeedItem {
  type: 'event' | 'replay'
  topic: string
  stream: string
  seq: number
  data: Envelope
  ts?: string
  id?: string // replay request id
}

export interface CommandAccepted {
  accepted: boolean
  command_id: string
  subject: string
  stream: string
  seq: number
  duplicate: boolean
}
