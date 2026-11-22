// Catalog product actions shared by the product card and Catalog › Edit: switch a SKU on or off (a personal-use
// licence needs the owner's word that they hold the commercial rights), and delete it.
// Delete asks the gateway first what still refers to the SKU: with orders, stock or a store listing it is never
// deleted (order history, margins and reports keep pointing at it), and the dialog offers to switch it off instead.
import { t } from '@/i18n'
import { errorText } from '@/lib/api'
import type { CatalogProduct, ProductReferences } from '@/lib/catalog'
import { useCatalog } from '@/stores/catalog'
import { useConfirm } from '@/stores/confirm'
import { useToasts } from '@/stores/toasts'

// What keeps a SKU (orders, finished stock, store listings), as one phrase for the dialog.
export function keptBy(r: ProductReferences): string {
  return [
    r.orders ? t('catalog.keepOrders', { n: r.orders }) : '',
    r.stock ? t('catalog.keepStock') : '',
    r.store_maps ? t('catalog.keepStores', { n: r.store_maps }) : '',
  ].filter(Boolean).join(t('catalog.keepAnd'))
}

export function useProductActions() {
  const catalog = useCatalog()
  const confirm = useConfirm()
  const toasts = useToasts()

  async function setActive(p: CatalogProduct, active: boolean): Promise<boolean> {
    // A licence that does not allow selling prints: on sale only with the owner's word that they hold the rights.
    const rights = active && !p.active && p.licence_status === 'non_commercial'
    if (rights) {
      const ok = await confirm.ask({
        title: t('catalog.rightsTitle', { sku: p.sku }),
        text: t('catalog.rightsText', { licence: p.licence_label || p.licence || '' }),
        confirmLabel: t('catalog.rightsOn'),
        requireCheck: t('catalog.rightsConfirm'),
      })
      if (!ok) return false
    }
    try {
      await catalog.setActive(p.sku, active, rights)
      toasts.info(t(active ? 'catalog.switchedOn' : 'catalog.switchedOff', { sku: p.sku }),
        t(active ? 'catalog.switchedOnText' : 'catalog.switchedOffText'))
      catalog.reloadSoon()
      return true
    } catch (err) {
      toasts.error(t('catalog.notSwitched', { sku: p.sku }), errorText(err))
      return false
    }
  }

  /** True when the delete command was accepted (the agent confirms on the live feed). */
  async function remove(p: CatalogProduct): Promise<boolean> {
    let refs: ProductReferences
    try {
      refs = await catalog.references(p.sku)
    } catch (err) {
      toasts.error(t('catalog.notDeleted', { sku: p.sku }), errorText(err))
      return false
    }
    if (refs.blocked) {
      const ok = await confirm.ask({
        title: t('catalog.keepTitle', { sku: p.sku }),
        text: t(p.active ? 'catalog.keepText' : 'catalog.keepTextOff', { what: keptBy(refs) }),
        confirmLabel: p.active ? t('catalog.switchOff') : t('catalog.keepOk'),
      })
      if (ok && p.active) await setActive(p, false)
      return false
    }
    const ok = await confirm.ask({
      title: t('catalog.deleteTitle', { sku: p.sku }),
      text: t('catalog.deleteText', { name: p.name }),
      confirmLabel: t('catalog.deleteConfirm'),
      danger: true,
    })
    if (!ok) return false
    try {
      await catalog.remove(p.sku)
      toasts.info(t('catalog.deleting', { sku: p.sku }), t('catalog.deletingText'))
      catalog.reloadSoon()
      return true
    } catch (err) {
      toasts.error(t('catalog.notDeleted', { sku: p.sku }), errorText(err))
      return false
    }
  }

  return { setActive, remove }
}
