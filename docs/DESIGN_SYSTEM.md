# Yigdesk design system

## Design language: Evidence Ledger

Yigdesk should feel like a trusted editorial instrument rather than a generic dashboard: warm archival paper, precise proof typography, compact source metadata, and a small number of high-signal colors.

The memorable interaction is the transition from a quiet read-only ledger into one of two explicit terminal states:

- **evidence green** means a value or packet is proven;
- **refusal coral** means the system stopped because evidence is incomplete.

Neither color is decorative. Neutral surfaces carry all ordinary content.

## Typography

| Role | Stack | Use |
| --- | --- | --- |
| Display | Palatino Linotype, Book Antiqua, Palatino, Iowan Old Style, Georgia | Hero, panel titles, decision values, narrative copy |
| Interface | Segoe UI Variable Text, Avenir Next, Segoe UI, Helvetica Neue | Controls, labels, compact explanatory text |
| Proof data | Cascadia Code, SFMono-Regular, Roboto Mono, Consolas | Object ids, fingerprints, cell addresses, status chips |

Headings use balanced wrapping. Body copy uses pretty wrapping. Changing figures and table values use tabular numerals.

## Color tokens

| Token | Value | Meaning |
| --- | --- | --- |
| Canvas | <code>#ebe8df</code> | Archival workspace |
| Raised surface | <code>#fffdf8</code> | Primary decision sheet |
| Ink | <code>#12211d</code> | Main text |
| Forest | <code>#16483b</code> | Action, verified structure, brand |
| Evidence | <code>#dafa38</code> | Proven/complete signal only |
| Hold | <code>#b94432</code> | Refusal signal only |
| Source | <code>#276782</code> | Incoming source identity |

Small text and status combinations are kept at or above WCAG AA contrast. Focus rings always use forest on light surfaces.

## Surfaces and spacing

- The three-panel workspace is one instrument with a shared 14px outer radius and one-pixel internal seams.
- Nested cards use 8px/4px concentric radii.
- Elevation uses layered transparent shadows; borders remain only for structural separators and table rows.
- Controls have a minimum 40px hit area; primary actions and scenario choices are 48px tall.

## Motion

- Page reveal is a one-shot 12px rise with a short blur.
- Interactive changes use interruptible transitions on explicit properties only.
- Buttons press to <code>scale(0.96)</code>.
- Loading swaps the action arrow for a spinner through opacity, blur, and <code>0.25 -&gt; 1</code> scale.
- Reduced-motion preference collapses animations to effectively instant state changes.

## Responsive behavior

- Three columns above 1180px.
- Two primary columns plus a full-width evidence panel from 821px to 1180px.
- Stacked instrument cards at 820px and below.
- At 600px, the primary metric spans the row while secondary metrics remain comparable side by side.

Reference captures:

- [Desktop complete evidence](images/yigdesk-ready.png)
- [Mobile complete evidence](images/yigdesk-mobile.png)
- [Desktop refusal state](images/yigdesk-hold.png)
