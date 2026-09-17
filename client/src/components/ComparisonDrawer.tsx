import { useState } from 'react'
import { X, Globe, Tag, CalendarDays, Zap, Newspaper } from 'lucide-react'
import type { EventComparison } from '../api/client'
import EvidenceDrawer from './EvidenceDrawer'
import { badgeStyleFor } from './materialityBands'
import './EvidenceDrawer.css'

const CATEGORY_COLORS: Record<string, string> = {
  Economic: '#10b981',
  Diplomacy: '#06b6d4',
  Military: '#ef4444',
  Social: '#8b5cf6',
}

const COUNTRY_COLORS: Record<string, string> = {
  China: '#e74c3c',
  Russia: '#3498db',
  Iran: '#f39c12',
  Turkey: '#9b59b6',
  'United States': '#2ecc71',
}

interface ComparisonDrawerProps {
  comp: EventComparison
  onClose: () => void
}

/**
 * Slide-in comparison panel: one event as each tracking country's pipeline
 * narrated it, side by side — same drawer grammar as the initiative-ledger
 * evidence panel. Each country section can open the evidence drawer on top
 * to trace that country's sourcing for the event.
 */
export default function ComparisonDrawer({ comp, onClose }: ComparisonDrawerProps) {
  const [evidenceFor, setEvidenceFor] = useState<string | null>(null)

  const narratives = Object.entries(comp.country_narratives || {})

  return (
    <>
      <div className="ev-overlay" onClick={onClose}>
        <aside className="ev-drawer" onClick={(e) => e.stopPropagation()}>
          <div className="ev-header" style={{ borderTopColor: '#1a365d' }}>
            <div className="ev-header-row">
              <span className="ev-actor" style={{ background: '#1a365d' }}>
                {comp.country_count} country perspectives
              </span>
              <button className="ev-close" onClick={onClose} aria-label="Close">
                <X size={18} />
              </button>
            </div>
            <h2>{comp.event_name}</h2>
            <div className="ev-meta">
              {comp.latest_date && (
                <span><CalendarDays size={13} /> {comp.latest_date}</span>
              )}
              {comp.avg_materiality != null && (
                <span style={badgeStyleFor(comp.avg_materiality)}>
                  <Zap size={11} /> avg material {comp.avg_materiality.toFixed(1)}/10
                </span>
              )}
            </div>
          </div>

          <div className="ev-body">
            {narratives.length === 0 && (
              <div className="ev-loading">No per-country narratives recorded for this event.</div>
            )}

            {narratives.map(([country, narr]) => {
              const accent = COUNTRY_COLORS[country] || '#94a3b8'
              return (
                <div key={country} className="cmp-country" style={{ borderLeft: `4px solid ${accent}` }}>
                  <div className="cmp-country-head">
                    <span className="cmp-country-name" style={{ color: accent }}>
                      <Globe size={14} /> {country}
                    </span>
                    {narr.material_score != null && (
                      <span className="ev-chip" title="materiality as scored on this country's file">
                        {narr.material_score.toFixed(1)}
                      </span>
                    )}
                    {narr.period_start && (
                      <span className="cmp-country-date">{narr.period_start}</span>
                    )}
                  </div>

                  {narr.overview && <p className="ev-desc">{narr.overview}</p>}
                  {narr.outcomes && (
                    <p className="ev-desc cmp-outcomes">Outcomes: {narr.outcomes}</p>
                  )}

                  <div className="cmp-country-foot">
                    <div className="ev-source-list">
                      {Object.entries(narr.count_by_category || {})
                        .sort(([, a], [, b]) => b - a)
                        .slice(0, 3)
                        .map(([cat, count]) => (
                          <span key={cat} className="ev-chip" style={{
                            backgroundColor: `${CATEGORY_COLORS[cat] || '#94a3b8'}18`,
                            color: CATEGORY_COLORS[cat] || '#475569',
                          }}>
                            <Tag size={9} style={{ marginRight: 3 }} />{cat} ({count})
                          </span>
                        ))}
                    </div>
                    <button className="cmp-trace" onClick={() => setEvidenceFor(country)}>
                      <Newspaper size={13} /> Trace sources
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        </aside>
      </div>

      {evidenceFor && (
        <EvidenceDrawer
          name={comp.event_name}
          actor={evidenceFor}
          onClose={() => setEvidenceFor(null)}
        />
      )}
    </>
  )
}
