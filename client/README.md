# Soft Power Analytics — React Frontend

Modern React + TypeScript + Vite single-page application for the Soft Power
Analytics platform. This README covers the frontend only — for backend,
database, and pipeline documentation see [../CLAUDE.md](../CLAUDE.md) and
[../services/PIPELINE_REFERENCE.md](../services/PIPELINE_REFERENCE.md).

## Tech Stack

- **React 19** + **TypeScript** (strict mode), built with **Vite 7**
- **react-router-dom 7** — client-side routing
- **@tanstack/react-query 5** — server-state fetching and caching
- **axios** — HTTP client (all calls go through `src/api/client.ts`, base URL `/api`)
- **recharts** — charts and visualizations
- **react-markdown** + **remark-gfm** — rendered markdown (chat answers, reports, intel report viewer)
- **sonner** — toast notifications
- **lucide-react** — icons

## Development Setup

```bash
cd client
npm install
npm run dev        # Vite dev server on http://localhost:5000 (strictPort)
```

The dev server proxies `/api/*` to the FastAPI server, which must be running
separately. The proxy target is `http://127.0.0.1:${API_PORT}` — Vite reads
`API_PORT` from the root `.env` at startup and falls back to `5001` if it is
not set (see [vite.config.ts](vite.config.ts)). Start the backend from the
project root:

```bash
uvicorn server.main:app --host 0.0.0.0 --port 5001 --reload
# or match your .env: --port $API_PORT
```

### Scripts

```bash
npm run dev       # Dev server with hot reload (port 5000)
npm run build     # tsc -b && vite build → dist/
npm run preview   # Preview the production bundle locally
npm run lint      # ESLint
```

## Production Build

`npm run build` type-checks and emits an optimized bundle to `client/dist/`.
In production there is no separate frontend server: `server/main.py` serves
`client/dist/` as static files at the site root, with API routes under
`/api/*`, from a single FastAPI process.

**Docker:** the default `docker-compose.yml` publishes the combined app + API
on host port `${API_PORT:-7001}` and the Streamlit dashboard on
`${DASHBOARD_PORT:-8501}`.

## Route Map

All routes render inside `Layout` and sit behind `ProtectedRoute`
(enterprise gateway auth). Defined in [src/App.tsx](src/App.tsx).

### Core browsing

| Route | Page | Shows |
|---|---|---|
| `/` | Dashboard | Landing overview: volume trends, top countries, categories |
| `/documents` | Documents | Browse/filter the document corpus |
| `/events` | Events | Canonical event listing |
| `/events/:eventId` | EventDetailPage | Single event: narrative, mentions, sources |
| `/events/:eventId/across-periods` | CrossPeriodView | Event rollups across daily/weekly/monthly periods |
| `/events/comparison` | CountryComparison | Cross-country event comparison with slide-in perspective drawer |
| `/events/materiality` | MaterialityHeatmap | Materiality-scored event heatmap |
| `/timeline` | TimelineComparison | Side-by-side activity timelines |
| `/categories` | Categories | Category/subcategory breakdowns |

### Countries & bilateral

| Route | Page | Shows |
|---|---|---|
| `/influencer/:country` | InfluencerPage | Single initiating country profile |
| `/bilateral` | BilateralRelationships | Influencer–recipient pair browser |
| `/bilateral/:influencer/:recipient` | BilateralPage | One bilateral relationship in depth |
| `/bilateral-metrics/:influencer/:recipient` | BilateralMetricsPage | Metrics for one pair |
| `/competing/:recipient` | CompetingInfluencePage | Competing initiators for one recipient |
| `/entity/:entityId` | EntityProfilePage | Canonical entity profile (roles, activity, documents) |

### Metrics

| Route | Page | Shows |
|---|---|---|
| `/metrics` | OverallMetrics | Corpus-wide metrics |
| `/metrics/influencer/:country` | InfluencerMetricsPage | Initiator-side metrics |
| `/metrics/recipient/:country` | RecipientMetricsPage | Recipient-side metrics |

### Summaries & reports

| Route | Page | Shows |
|---|---|---|
| `/summaries` | Summaries | Event summary browser |
| `/document-summaries` | DocumentSummariesPage | Period document summaries |
| `/document-summaries/detail` | SummaryDetailPage | One document summary |
| `/bilateral-summaries` | BilateralSummariesPage | Bilateral relationship summaries |
| `/bilateral-summaries/detail` | BilateralSummaryDetailPage | One bilateral summary |
| `/report` | ReportPage | Generated analytical reports (with Word export) |
| `/intel-reports` | IntelReportsPage | Curated intel report library |
| `/intel-reports/:slug` | IntelReportViewerPage | Rendered intel report |

### Interactive & misc

| Route | Page | Shows |
|---|---|---|
| `/chat` | ChatPage | RAG chat over the corpus with citations |
| `/agent` | AgentPage | OSINT-style agent (tool-using conversational analyst) |
| `/drilldown` | DrilldownPage | Ad-hoc metric drilldown (via `useDrilldown`) |
| `/alerts` | AlertsPage | Alert rules and triggered alerts |
| `/survey` | SurveyPage | User feedback survey |
| `/about` | AboutMethodologyPage | Methodology and caveats |
| `/whitepaper` | WhitePaperPage | Platform whitepaper |

### Role-gated (ProtectedRoute `requiredRole`)

| Route | Page | Role | Shows |
|---|---|---|---|
| `/ingestion` | DataIngestionPage | analyst+ | Document ingestion job UI |
| `/admin/users` | UserManagementPage | admin | User/role management |

## Key Components (`src/components/`)

- **Layout** — app shell: sidebar navigation, header, outlet for pages
- **ProtectedRoute** — auth wrapper; optional `requiredRole` gating (analyst/admin)
- **ComparisonDrawer** — slide-in perspective drawer on Country Comparison
- **EvidenceDrawer** — slide-in source-evidence panel (same pattern as ComparisonDrawer)
- **ProjectDrawer** — slide-in drawer for project/initiative details
- **AlertBell** — header notification bell for triggered alerts
- **AlertRuleForm** — create/edit alert rules
- **ChatReportModal** — promote a chat exchange into a generated report
- **DataCoverageBadge** — inline badge flagging data-coverage caveats
- **MapView** — geographic visualization
- **PageGuide** (+ `pageGuides.ts`) — contextual per-page help
- **ReportFigure** (+ `reportFigures/` builders and CSV export) — standardized report figures
- **ValidationIndicator** — shows LLM-validation status on events
- **tour/** — guided product tour (TourContext, TourOverlay, TourButton, steps)
- `materialityBands.ts` — shared materiality band definitions

## Project Structure

```
client/
├── src/
│   ├── api/
│   │   └── client.ts       # Axios instance + typed API interfaces
│   ├── assets/             # Static assets bundled by Vite
│   ├── components/         # Reusable components (see above)
│   │   ├── reportFigures/  # Report figure builders + CSV export
│   │   └── tour/           # Guided tour
│   ├── contexts/
│   │   ├── AuthContext.tsx             # User/session/role state
│   │   └── ReportGenerationContext.tsx # Cross-page report generation state
│   ├── hooks/
│   │   └── useDrilldown.ts # Drilldown query state
│   ├── pages/              # One component per route (see route map)
│   ├── types/              # Shared TypeScript types
│   ├── App.tsx             # Route definitions
│   └── main.tsx            # Entry point
├── public/                 # Static files copied as-is
├── dist/                   # Production build output (gitignored)
├── package.json
├── vite.config.ts          # Dev port 5000 + /api proxy
└── tsconfig.json
```

## API Reference

The frontend talks exclusively to the FastAPI backend under `/api`. For the
live, complete endpoint list, use FastAPI's interactive docs on a running
server: `http://localhost:${API_PORT:-5001}/docs` (or port 7001 under the
default Docker stack). Endpoint implementations live in `server/main.py` and
`server/routers/`.
