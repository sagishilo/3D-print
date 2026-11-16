import 'vue-router'

declare module 'vue-router' {
  interface RouteMeta {
    public?: boolean
    title?: string
    phase?: number
    blurb?: string
    /** The screen's page tour (lib/page-tours.ts), started from the top bar. */
    tour?: import('@/lib/page-tours').TourId
  }
}
