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

export type PrinterStatus = 'idle' | 'printing' | 'paused' | 'finished' | 'failed' | 'offline'

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
}

export interface Printer {
  id: string
  vendor: string
  protocol: string
  model: string
  simulated: boolean
  bed_mm: [number, number]
  nozzle_mm: number
  material: string
  color: string
  online: boolean
  busy: boolean
  bed_clear: boolean
  updated_at: string
  state: PrinterState | null
  camera: boolean
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
