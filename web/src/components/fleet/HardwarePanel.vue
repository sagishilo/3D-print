<script setup lang="ts">
// Deep hardware readings of one printer (PrinterState.hardware): temperatures, fans, motion, nozzle, connection,
// health codes and the AMS. Vendors report different things; missing values show as "—".
import { computed } from 'vue'

import StatusPill from '@/components/ui/StatusPill.vue'
import { fanRows, humanize, wifiQuality } from '@/lib/fleet'
import { temp } from '@/lib/format'
import type { AmsUnit, Printer } from '@/lib/types'

const props = defineProps<{ printer: Printer }>()

const s = computed(() => props.printer.state)
const hw = computed(() => s.value?.hardware ?? null)
const fans = computed(() => fanRows(hw.value))
const wifi = computed(() => wifiQuality(hw.value?.wifi_dbm))
const klipper = computed(() => props.printer.vendor === 'moonraker')

function amsMeta(unit: AmsUnit): string {
  return (unit.humidity_level != null ? ` · humidity index ${unit.humidity_level}` : '') + (unit.temp != null ? ` · ${temp(unit.temp)} °C` : '')
}
</script>

<template>
  <div class="hardware">
    <p v-if="!hw" class="empty">{{ printer.id }} has not reported hardware details yet{{ printer.online ? '' : ' (offline)' }}.</p>
    <template v-else>
      <div class="cards">
        <section class="card" data-testid="hw-temps">
          <h3>Temperatures</h3>
          <dl>
            <dt>Nozzle</dt>
            <dd class="mono">{{ temp(s?.nozzle_temp) }} / {{ temp(s?.nozzle_target) }} °C</dd>
            <dt>Bed</dt>
            <dd class="mono">{{ temp(s?.bed_temp) }} / {{ temp(s?.bed_target) }} °C</dd>
            <dt>Chamber</dt>
            <dd class="mono">{{ hw.chamber_temp != null ? `${temp(hw.chamber_temp)} °C` : 'no sensor' }}</dd>
          </dl>
        </section>

        <section class="card" data-testid="hw-fans">
          <h3>Fans</h3>
          <div v-for="f in fans" :key="f.key" class="fan">
            <span>{{ f.label }}</span>
            <div class="bar" role="meter" :aria-valuenow="f.pct" aria-valuemin="0" aria-valuemax="100" :aria-label="`${f.label} fan`">
              <div :style="{ width: `${f.pct}%` }" />
            </div>
            <span class="mono pct">{{ f.pct }}%</span>
          </div>
          <p v-if="!fans.length" class="muted">Not reported</p>
        </section>

        <section class="card">
          <h3>Motion</h3>
          <dl>
            <dt>Speed</dt>
            <dd>{{ hw.speed_mode ? humanize(hw.speed_mode) : '—' }}<span v-if="hw.speed_pct != null" class="mono"> · {{ hw.speed_pct }}%</span></dd>
            <template v-if="klipper">
              <dt>Homed axes</dt>
              <dd class="mono">{{ hw.homed_axes ? hw.homed_axes.toUpperCase() : 'not homed' }}</dd>
              <dt>Bed mesh</dt>
              <dd>{{ hw.bed_mesh ?? 'none loaded' }}</dd>
            </template>
            <dt>Doing</dt>
            <dd>{{ hw.stage ? humanize(hw.stage) : s?.status === 'printing' ? 'Printing' : '—' }}</dd>
          </dl>
        </section>

        <section class="card">
          <h3>Nozzle</h3>
          <dl>
            <dt>Diameter</dt>
            <dd class="mono">{{ hw.nozzle_diameter != null ? `${hw.nozzle_diameter} mm` : '—' }}</dd>
            <dt>Type</dt>
            <dd>{{ humanize(hw.nozzle_type) }}</dd>
          </dl>
        </section>

        <section class="card" data-testid="hw-connection">
          <h3>Connection</h3>
          <dl>
            <dt>Protocol</dt>
            <dd>{{ printer.protocol }}</dd>
            <dt>Firmware</dt>
            <dd class="mono">{{ hw.firmware ?? '—' }}</dd>
            <template v-if="hw.wifi_dbm != null">
              <dt>Wi-Fi</dt>
              <dd><span class="mono">{{ hw.wifi_dbm }} dBm</span> <StatusPill :tone="wifi.tone" :label="wifi.label" /></dd>
            </template>
            <template v-if="hw.host_load != null">
              <dt>Host load</dt>
              <dd class="mono">{{ hw.host_load.toFixed(2) }}</dd>
            </template>
          </dl>
        </section>

        <section class="card" data-testid="hw-health">
          <h3>Health</h3>
          <p v-if="!hw.hms.length && !s?.error_code" class="ok">No warnings reported</p>
          <ul v-else class="codes">
            <li v-if="s?.error_code" class="mono">Printer error {{ s.error_code }}</li>
            <li v-for="code in hw.hms" :key="code" class="mono">HMS {{ code }}</li>
          </ul>
        </section>
      </div>

      <section v-for="unit in hw.ams" :key="unit.unit" class="card ams" data-testid="hw-ams">
        <h3>AMS {{ unit.unit + 1 }}<span class="muted">{{ amsMeta(unit) }}</span></h3>
        <ul class="trays">
          <li v-for="t in unit.trays" :key="t.slot">
            <span class="swatch" :style="{ background: t.color ?? 'transparent' }" aria-hidden="true" />
            <span class="slot">Slot {{ t.slot + 1 }}</span>
            <span class="material">{{ t.material ?? 'empty' }}</span>
            <div class="bar small" role="meter" :aria-valuenow="t.remaining_pct ?? 0" aria-valuemin="0" aria-valuemax="100" :aria-label="`Slot ${t.slot + 1} remaining`">
              <div :style="{ width: `${t.remaining_pct ?? 0}%` }" :class="{ low: (t.remaining_pct ?? 100) < 15 }" />
            </div>
            <span class="mono pct">{{ t.remaining_pct != null ? `${t.remaining_pct}%` : '?' }}</span>
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>

<style scoped>
.hardware {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(220px, 100%), 1fr));
  gap: 12px;
}
.card {
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--bg);
}
h3 {
  font: 600 13px var(--font);
}
dl {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: 4px 12px;
  margin: 0;
  font-size: 13px;
}
dt {
  color: var(--muted);
}
dd {
  margin: 0;
  text-align: end;
}
.fan {
  display: grid;
  grid-template-columns: 90px 1fr 40px;
  gap: 8px;
  align-items: center;
  font-size: 12px;
}
.bar {
  height: 6px;
  border-radius: 999px;
  background: var(--surface-2);
  overflow: hidden;
}
.bar > div {
  height: 100%;
  background: var(--accent);
  border-radius: 999px;
  transition: width 0.4s ease;
}
.bar > div.low {
  background: var(--warn);
}
.pct {
  text-align: end;
  font-size: 12px;
}
.ok {
  color: var(--ok);
  font-size: 13px;
}
.codes {
  margin: 0;
  padding-inline-start: 16px;
  color: var(--warn);
  font-size: 12px;
}
.trays {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.trays li {
  display: grid;
  grid-template-columns: 16px 52px 60px 1fr 40px;
  gap: 8px;
  align-items: center;
  font-size: 12px;
}
.swatch {
  width: 16px;
  height: 16px;
  border-radius: 4px;
  border: 1px solid var(--border);
}
.slot {
  color: var(--muted);
}
</style>
