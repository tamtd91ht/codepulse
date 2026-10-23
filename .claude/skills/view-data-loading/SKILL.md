---
name: view-data-loading
description: Plan and review how a screen, page or component loads its data from APIs - critical path vs deferred, one request per resource, independent requests in parallel, nothing blocking the first render that does not have to, fetch on need, cancel and dedupe. Use when adding or changing data fetching in a web or mobile view, when a screen shows a spinner for too long, or when the network panel shows waterfalls or repeated calls.
---

# View data loading (request discipline for a screen)

Most slow screens are not slow APIs. They are seven calls made one after another where two
were needed to draw the first frame, the same resource fetched three times by three
components, or a non-essential widget whose request blocks the whole page behind a spinner.
This skill is the checklist you run **before** writing data-fetching code for a view and
**when** reviewing it.

## 1. Draw the request map first

Before any `fetch`, list every piece of data the view shows and classify it:

| Column | Meaning |
|---|---|
| resource | the API / query (one row per distinct resource, not per component) |
| needed for | `first paint` (the user cannot do anything without it) · `enhancement` (a widget, counter, suggestion) · `on interaction` (a tab, a modal, the next page) |
| depends on | another row's result (a real data dependency), or `-` |
| owner | the one place that fetches it (route loader, page component, store, query hook) |
| cache key | how a second consumer finds the same result instead of refetching |

Rules the map must satisfy before you write code:

- Every resource appears **once**. Two components needing the same data share one request
  through a cache / store / query client, never two calls.
- Everything in `first paint` with `depends on = -` is fetched **in parallel** in one place.
- Nothing in `enhancement` or `on interaction` is awaited before the first render.
- A chain (`depends on` set) exists only when the second request genuinely needs the first
  result (an id, a token). "It was convenient to await" is not a dependency.

If the map has more than three `first paint` rows, ask whether the backend can offer one
aggregated endpoint or a batched query; a view that needs seven calls to render is a design
smell, not a frontend problem to paper over.

## 2. Load the critical path in parallel, once

- Fire independent first-paint requests together: `Promise.all` / `Promise.allSettled`, a
  route loader that returns several promises, `forkJoin`, a query client with several
  `useQuery` calls mounted at once. Sequential `await` lines are a waterfall unless the second
  needs the first.
- Fetch at the **highest common owner** (route loader, page, server component, store action),
  not inside each leaf component. Leaves read from the cache / props.
- Keep the first-paint payload small: ask only for the fields the first frame shows;
  everything else is a deferred row in the map.

## 3. Never let secondary data block the main flow

- Render the shell and the first-paint data as soon as they are ready. Deferred rows render
  their own placeholder (skeleton, "—", collapsed) and fill in when their request resolves:
  `Suspense` boundaries, streaming / deferred loader data, `useLazyAsyncData`, independent
  observables, per-widget loading state. One spinner over the whole page because one widget
  is slow is the bug this skill exists for.
- A failed secondary request shows an inline fallback ("could not load recommendations") and
  never throws the page into an error state. Use `allSettled` semantics, not `all`, where one
  failure must not sink the rest.
- The user can interact (scroll, click, type) while deferred data is still loading.

## 4. Fetch on need, not on mount

- Content behind a tab, accordion, modal, "load more" or below the fold is fetched when the
  user gets there (or prefetched on intent: hover, focus, `IntersectionObserver` near the
  viewport), not when the page mounts.
- Do not fetch for a view the user has not navigated to yet unless you are deliberately
  prefetching a likely next step, and then only when the device is idle and the data is small.
- Polling and live updates have an explicit interval, pause when the tab is hidden, and stop
  when the view unmounts.

## 5. Dedupe, cache, cancel, order

- One in-flight request per cache key: a query client, SWR-style hook or store action with
  request deduplication. Three components mounting at once must produce one network call.
- Decide a staleness policy per resource (`staleTime` / TTL / stale-while-revalidate). Data
  that changes once a day is not refetched on every mount or focus.
- Cancel on unmount and on parameter change (`AbortController`, cancellation tokens,
  `switchMap`); a late response from a previous search term must not overwrite the current
  result (race condition).
- Mutations update the cache (optimistic or invalidate the exact keys), not "refetch
  everything".
- Effects and watchers that fetch have correct dependencies; a fetch that runs on every render
  is a bug even when the result looks right.

## 6. Framework pointers (use what the project already has)

| Stack | Parallel first paint | Deferred / non-blocking | Dedupe / cache |
|---|---|---|---|
| React / Next.js | route loader or server component with `Promise.all`; several `useQuery` at once | `Suspense` boundaries, streaming, `useDeferredValue`, lazy components | TanStack Query / SWR / RTK Query keys, RSC cache |
| Vue / Nuxt | `useAsyncData` with `Promise.all`, parallel `await` in `setup` | `useLazyAsyncData`, `<Suspense>`, `lazy: true` | Nuxt payload / `getCachedData`, Pinia store, TanStack Query |
| Angular | `forkJoin` / `combineLatest`, route resolvers | async pipe per widget, `defer` blocks, signals | shared service with `shareReplay`, store selectors |
| Svelte / SvelteKit | `load` returning several promises | streamed promises in `load`, `{#await}` per block | `depends` / `invalidate`, stores |
| React Native / Expo | same as React; batch on screen focus | screen shell + per-section loaders | TanStack Query with persistence, MMKV / AsyncStorage |
| Mobile / desktop native | structured concurrency (`async let`, `coroutineScope`) | per-section state, placeholders | repository layer with in-memory cache |

Follow the project's existing choice; do not introduce a second data-fetching library.

## 7. Review checklist (what to look for in a diff)

- [ ] A request map exists (in the PR description or a comment) or the diff is one obvious call.
- [ ] No resource fetched twice for one view (search the diff for the same URL / query key).
- [ ] Independent first-paint requests start together; every sequential `await` has a real dependency.
- [ ] The first render does not await enhancement or on-interaction data.
- [ ] Secondary failures degrade a widget, not the page.
- [ ] Hidden / off-screen content is fetched on need.
- [ ] Requests are cancelled on unmount and on parameter change; stale responses cannot win.
- [ ] Staleness policy stated; no refetch-on-every-render; mutations invalidate exact keys.
- [ ] Measured: network panel shows no waterfall for first paint and the request count per
      view is at or under the agreed budget (state the number).

## 8. When reviewing an existing slow screen

1. Open the network panel, reload the screen, read the waterfall: count requests, mark which
   finished before first paint, which blocked it, which repeated.
2. Build the request map from what you see; the gap between "needed for first paint" and
   "awaited before first paint" is the fix list.
3. Fix in this order: remove duplicates → parallelise independent first-paint calls → move
   enhancement data behind its own placeholder → lazy-load hidden content → add cancellation
   and staleness policy. Measure again after each step and keep the numbers in the PR.
