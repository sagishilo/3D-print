import { describe, expect, it } from 'vitest'

// @ts-expect-error - plain JS module of the capture extension (../extension), no type declarations
import { capturePayload, farmOrigin, groupFromTitle, newKey, postUrl } from '../../extension/lib.js'

describe('capture extension helpers', () => {
  it('takes the group name from the page title', () => {
    expect(groupFromTitle('(3) הורים בחיפה | Facebook')).toBe('הורים בחיפה')
    expect(groupFromTitle('TCG Collectors IL - Facebook')).toBe('TCG Collectors IL')
    expect(groupFromTitle(undefined)).toBe('')
  })

  it('keeps an https post link without trackers, else the page', () => {
    expect(postUrl('https://www.facebook.com/groups/1/posts/2/?__cft__[0]=abc&__tn__=R', 'https://www.facebook.com/groups/1'))
      .toBe('https://www.facebook.com/groups/1/posts/2/')
    expect(postUrl(undefined, 'https://www.facebook.com/groups/1/?fbclid=x')).toBe('https://www.facebook.com/groups/1/')
    expect(postUrl('javascript:alert(1)', 'http://insecure.example')).toBe('')
  })

  it('turns what the person typed into the dashboard origin, https only', () => {
    expect(farmOrigin('192.168.50.10')).toBe('https://192.168.50.10')
    expect(farmOrigin('https://farm.local/')).toBe('https://farm.local')
    expect(farmOrigin('http://192.168.50.10')).toBeNull()
    expect(farmOrigin('')).toBeNull()
  })

  it('sends only the selected text, capped, and nothing when nothing is selected', () => {
    const p = capturePayload('  מחפשת שלט  ', { pageUrl: 'https://www.facebook.com/groups/1' }, { title: 'הורים | Facebook' })
    expect(p).toEqual({ post_text: 'מחפשת שלט', group_name: 'הורים', post_url: 'https://www.facebook.com/groups/1' })
    expect(capturePayload('   ', {}, {})).toBeNull()
    expect(capturePayload('א'.repeat(9000), {}, {}).post_text).toHaveLength(5000)
    expect(newKey()).toMatch(/^ext-[0-9a-f]{24}$/)
  })
})
