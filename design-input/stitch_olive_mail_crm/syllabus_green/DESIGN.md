---
name: Syllabus Green
colors:
  surface: '#f8faf6'
  surface-dim: '#d9dbd7'
  surface-bright: '#f8faf6'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f2f4f0'
  surface-container: '#edeeea'
  surface-container-high: '#e7e9e5'
  surface-container-highest: '#e1e3df'
  on-surface: '#191c1a'
  on-surface-variant: '#424845'
  inverse-surface: '#2e312f'
  inverse-on-surface: '#eff1ed'
  outline: '#727875'
  outline-variant: '#c2c8c4'
  surface-tint: '#4b635a'
  primary: '#344b43'
  on-primary: '#ffffff'
  primary-container: '#4b635a'
  on-primary-container: '#c3ded2'
  inverse-primary: '#b2ccc1'
  secondary: '#596059'
  on-secondary: '#ffffff'
  secondary-container: '#dbe2d8'
  on-secondary-container: '#5d645d'
  tertiary: '#254b5b'
  on-tertiary: '#ffffff'
  tertiary-container: '#3e6374'
  on-tertiary-container: '#b8def2'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#cee9dd'
  primary-fixed-dim: '#b2ccc1'
  on-primary-fixed: '#082019'
  on-primary-fixed-variant: '#344b43'
  secondary-fixed: '#dee4db'
  secondary-fixed-dim: '#c2c8bf'
  on-secondary-fixed: '#171d17'
  on-secondary-fixed-variant: '#424942'
  tertiary-fixed: '#c2e8fc'
  tertiary-fixed-dim: '#a6cce0'
  on-tertiary-fixed: '#001f2a'
  on-tertiary-fixed-variant: '#254b5c'
  background: '#f8faf6'
  on-background: '#191c1a'
  surface-variant: '#e1e3df'
typography:
  display-lg:
    fontFamily: Inter
    fontSize: 57px
    fontWeight: '400'
    lineHeight: 64px
    letterSpacing: -0.25px
  headline-lg:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
    letterSpacing: 0px
  headline-lg-mobile:
    fontFamily: Inter
    fontSize: 28px
    fontWeight: '600'
    lineHeight: 36px
    letterSpacing: 0px
  title-lg:
    fontFamily: Inter
    fontSize: 22px
    fontWeight: '500'
    lineHeight: 28px
    letterSpacing: 0px
  body-lg:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '400'
    lineHeight: 24px
    letterSpacing: 0.5px
  body-md:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 20px
    letterSpacing: 0.25px
  label-lg:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '500'
    lineHeight: 20px
    letterSpacing: 0.1px
  label-md:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 16px
    letterSpacing: 0.5px
rounded:
  sm: 0.25rem
  DEFAULT: 0.5rem
  md: 0.75rem
  lg: 1rem
  xl: 1.5rem
  full: 9999px
spacing:
  base: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 32px
  gutter: 24px
  margin-mobile: 16px
  margin-desktop: 32px
---

## Brand & Style
This design system is built for a high-volume B2B SaaS environment where reliability and clarity are paramount. The aesthetic follows a **Modern Corporate** approach, heavily influenced by **Material Design 3 (MD3)** principles, utilizing tonal layering rather than heavy shadows to define interface hierarchy. 

The target audience consists of marketing professionals and operations managers who require a calm, non-fatiguing workspace for managing complex data. The emotional response is one of "governed efficiency"—the UI feels professional and sturdy, using a nature-inspired palette to reduce the stress associated with large-scale campaign deployments. Whitespace is used systematically to separate data-dense tables from navigational controls.

## Colors
The palette is centered on a "Calm Sage" spectrum to promote focus. 

- **Primary (#4B635A):** Used for key action buttons, active states, and primary branding.
- **Secondary (#727971):** A desaturated green-gray for less prominent UI elements and decorative icons.
- **Container Light (#D1E8E2):** Used for card backgrounds or "Selected" states in lists to provide a soft, low-contrast highlight.
- **Surface (#F8FAF9):** The primary background color, providing a crisp, clean canvas that is softer than pure white (#FFFFFF), which is reserved for elevated cards.
- **Functional Colors:** Error states should use a muted terracotta, and success states should use a vibrant forest green to maintain the organic theme.

## Typography
This design system utilizes **Inter** across all roles to ensure maximum legibility and a systematic, utilitarian feel. 

Hierarchy is established primarily through font weight and subtle color shifts (Primary Text vs. Secondary Text) rather than dramatic size changes. High-contrast text (#191C1B) is mandated for all body copy to ensure accessibility during long sessions of campaign editing. For data-heavy tables, use `body-md` to maximize information density without sacrificing readability.

## Layout & Spacing
The layout follows a **Fluid Grid** model with an 8px base unit. 

- **Desktop (1440px+):** 12-column grid with 24px gutters and 32px side margins. Side navigation is usually fixed at 280px.
- **Tablet (768px - 1439px):** 8-column grid with 24px gutters and 24px side margins.
- **Mobile (Up to 767px):** 4-column grid with 16px gutters and 16px side margins.

Content should be grouped into logical "sections" using 32px of vertical spacing (`xl`). Inside components, use 16px (`md`) for standard padding.

## Elevation & Depth
In accordance with MD3, depth is conveyed through **Tonal Layers** supplemented by very soft, ambient shadows. 

- **Level 0 (Surface):** The base background layer (#F8FAF9).
- **Level 1 (Low Elevation):** Cards and surfaces that sit just above the base. These use a pure white fill (#FFFFFF) and a subtle 1px border (#E1E3E1) rather than a shadow.
- **Level 2 (Active/Hover):** Applied to hovered cards or buttons. Uses a soft, diffused shadow: `0px 2px 6px rgba(75, 99, 90, 0.08)`.
- **Level 3 (Modals/Popovers):** Highest elevation. Uses a more pronounced shadow: `0px 8px 24px rgba(0, 0, 0, 0.12)`.

Avoid high-contrast black shadows; always tint shadows with a hint of the primary sage color to maintain palette harmony.

## Shapes
The design system employs a "Soft Professional" geometry. 

Standard components like input fields and buttons use a **0.5rem (8px)** radius. Larger structural containers, such as dashboard cards and modals, utilize **rounded-lg (16px)** to create a more approachable, modern SaaS feel. Icons should follow a "Rounded" or "Soft" style to match the UI's corner radius.

## Components
- **Buttons:** Primary buttons are solid Sage (#4B635A) with white text. Secondary buttons use an outlined style with a 1px border. Use "Pill" shapes (rounded-xl) for buttons to differentiate them from square-ish data containers.
- **Input Fields:** Filled style (MD3) with a light gray background and a bottom-border focus indicator in Sage. Ensure labels are always visible (no floating labels that disappear).
- **Cards:** White background, 16px corner radius, and a 1px soft gray border. Avoid shadows on cards unless they are interactive or hovered.
- **Chips:** Used for email tags or status indicators (e.g., "Sent", "Draft"). Use the Light Mint (#D1E8E2) background with dark green text.
- **Lists & Tables:** Use alternating row stripes (Zebra striping) using the Surface color (#F8FAF9) and pure white to manage large sets of subscriber data.
- **Progress Indicators:** For bulk sending, use a thick horizontal bar in Primary Sage with a transition animation that feels smooth and steady, reflecting system reliability.