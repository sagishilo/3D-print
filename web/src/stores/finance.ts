// Finance: the report of the chosen period, the 12-month trend, product margins, receivables, VAT, ledgers and
// settings. Bookings come from the finance agent; this store reloads when it reports a new ledger entry.
import { defineStore } from 'pinia'
import { ref, watch } from 'vue'

import { api, command, errorText } from '@/lib/api'
import type {
  ExpenseEntry,
  FinanceSettings,
  IncomeEntry,
  PeriodKind,
  NicheRow,
  ProductRow,
  Receivable,
  Report,
  TrendMonth,
  VatReport,
} from '@/lib/finance'

import { withNiche } from '@/lib/niches'
import { useNiche } from '@/stores/niche'

const thisMonth = () => new Date().toISOString().slice(0, 7)

export const useFinance = defineStore('finance', () => {
  const period = ref<PeriodKind>('month')
  const month = ref(thisMonth())
  const report = ref<Report | null>(null)
  const trend = ref<TrendMonth[]>([])
  const products = ref<ProductRow[]>([])
  const niches = ref<NicheRow[]>([])
  const receivables = ref<Receivable[]>([])
  const income = ref<IncomeEntry[]>([])
  const expenses = ref<ExpenseEntry[]>([])
  const vat = ref<VatReport | null>(null)
  const settings = ref<FinanceSettings | null>(null)
  const error = ref('')
  let timer: ReturnType<typeof setTimeout> | null = null
  const niche = useNiche()
  watch(() => niche.current, () => { if (report.value) void load() })

  async function load() {
    const q = new URLSearchParams({ period: period.value, month: month.value })
    try {
      const [r, t, p, n, rc, i, e, v, s] = await Promise.all([
        api<Report>(`/api/v1/finance/report?${q}`),
        api<{ months: TrendMonth[] }>(`/api/v1/finance/trend?month=${month.value}`),
        api<{ products: ProductRow[] }>(withNiche(`/api/v1/finance/products?${q}`, niche.current)),
        api<{ niches: NicheRow[] }>(`/api/v1/finance/niches?${q}`),
        api<{ orders: Receivable[] }>('/api/v1/finance/receivables'),
        api<{ entries: IncomeEntry[] }>(`/api/v1/finance/income?${q}`),
        api<{ entries: ExpenseEntry[] }>(`/api/v1/finance/expenses?${q}`),
        api<VatReport>(`/api/v1/finance/vat?month=${month.value}`),
        api<FinanceSettings>('/api/v1/finance/settings'),
      ])
      report.value = r
      trend.value = t.months
      products.value = p.products
      niches.value = n.niches
      receivables.value = rc.orders
      income.value = i.entries
      expenses.value = e.entries
      vat.value = v
      settings.value = s
      error.value = ''
    } catch (err) {
      error.value = errorText(err)
    }
  }

  function reloadSoon() {
    if (timer) clearTimeout(timer)
    timer = setTimeout(() => void load(), 400)
  }

  return {
    period, month, report, trend, products, niches, receivables, income, expenses, vat, settings, error, load, reloadSoon,
    recordPayment: (body: Record<string, unknown>) => command('payments.record', body),
    addExpense: (body: Record<string, unknown>) => command('expenses.create', body),
    saveSettings: (body: Record<string, unknown>) => command('finance.settings', body),
  }
})
