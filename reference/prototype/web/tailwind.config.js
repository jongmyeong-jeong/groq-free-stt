/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    container: {
      center: true,
      padding: '2rem',
      screens: { '2xl': '1400px' },
    },
    extend: {
      colors: {
        // === lonta Design System (DESIGN.md) ===
        ds: {
          // surfaces — use with bg-ds-*
          page: 'var(--color-bg-page)',
          card: 'var(--color-bg-card)',
          subpanel: 'var(--color-bg-subpanel)',
          hover: 'var(--color-bg-hover)',
          selected: 'var(--color-bg-selected)',
          backdrop: 'var(--color-backdrop)',
          // text — use with text-ds-*
          primary: 'var(--color-text-primary)',
          secondary: 'var(--color-text-secondary)',
          tertiary: 'var(--color-text-tertiary)',
          disabled: 'var(--color-text-disabled)',
          // border — use with border-ds-*
          subtle: 'var(--color-border-subtle)',
          strong: 'var(--color-border-strong)',
          // brand
          blue: 'var(--color-blue)',
          link: 'var(--color-link)',
          'focus-ring': 'var(--color-focus-ring)',
          // semantic
          success: 'var(--color-success)',
          warning: 'var(--color-warning)',
          error: 'var(--color-error)',
          // action
          action: 'var(--color-action-primary)',
          'on-action': 'var(--color-on-primary)',
          // pill bg/text pairs
          'pill-neutral': 'var(--color-pill-neutral-bg)',
          'pill-neutral-fg': 'var(--color-pill-neutral-text)',
          'pill-info': 'var(--color-pill-info-bg)',
          'pill-info-fg': 'var(--color-pill-info-text)',
          'pill-success': 'var(--color-pill-success-bg)',
          'pill-success-fg': 'var(--color-pill-success-text)',
          'pill-warning': 'var(--color-pill-warning-bg)',
          'pill-warning-fg': 'var(--color-pill-warning-text)',
          'pill-error': 'var(--color-pill-error-bg)',
          'pill-error-fg': 'var(--color-pill-error-text)',
        },
      },
      fontFamily: {
        sans: 'var(--font-sans)',
        mono: 'var(--font-mono)',
      },
      fontSize: {
        // lonta type scale (DESIGN.md §3)
        'ds-display':         ['32px', { lineHeight: '1.3', letterSpacing: '-0.03em', fontWeight: '700' }],
        'ds-page-title':      ['24px', { lineHeight: '1.3', letterSpacing: '-0.02em', fontWeight: '700' }],
        'ds-section-heading': ['20px', { lineHeight: '1.3', letterSpacing: '-0.02em', fontWeight: '600' }],
        'ds-sub-section':     ['18px', { lineHeight: '1.4', letterSpacing: '-0.01em', fontWeight: '600' }],
        'ds-card-title':      ['16px', { lineHeight: '1.4', letterSpacing: '-0.01em', fontWeight: '600' }],
        'ds-reading':         ['16px', { lineHeight: '1.7', letterSpacing: '0',       fontWeight: '400' }],
        'ds-ui':              ['14px', { lineHeight: '1.5', letterSpacing: '0',       fontWeight: '400' }],
        'ds-ui-medium':       ['14px', { lineHeight: '1.5', letterSpacing: '0',       fontWeight: '500' }],
        'ds-caption':         ['12px', { lineHeight: '1.4', letterSpacing: '0',       fontWeight: '500' }],
        'ds-mono-caption':    ['12px', { lineHeight: '1.4', letterSpacing: '0',       fontWeight: '400' }],
      },
      spacing: {
        'ds-0':  'var(--space-0)',
        'ds-1':  'var(--space-1)',
        'ds-2':  'var(--space-2)',
        'ds-3':  'var(--space-3)',
        'ds-4':  'var(--space-4)',
        'ds-5':  'var(--space-5)',
        'ds-6':  'var(--space-6)',
        'ds-8':  'var(--space-8)',
        'ds-10': 'var(--space-10)',
        'ds-12': 'var(--space-12)',
        'ds-16': 'var(--space-16)',
        'ds-20': 'var(--space-20)',
      },
      borderRadius: {
        // lonta
        'ds-input':   'var(--radius-input)',
        'ds-control': 'var(--radius-control)',
        'ds-card':    'var(--radius-card)',
        'ds-pill':    'var(--radius-pill)',
      },
      zIndex: {
        'ds-base':     'var(--z-base)',
        'ds-sticky':   'var(--z-sticky)',
        'ds-dropdown': 'var(--z-dropdown)',
        'ds-modal':    'var(--z-modal)',
        'ds-toast':    'var(--z-toast)',
        'ds-tooltip':  'var(--z-tooltip)',
      },
      maxWidth: {
        'ds-reading': 'var(--reading-max-width)',
      },
    },
  },
  plugins: [require('tailwindcss-animate')],
}
