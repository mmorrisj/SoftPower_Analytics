import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Globe, Zap, Calendar, ChevronDown, ChevronRight } from 'lucide-react'
import { fetchEventComparison } from '../api/client'
import type { EventComparison } from '../api/client'
import ComparisonDrawer from '../components/ComparisonDrawer'
import PageGuide from '../components/PageGuide'
import { badgeStyleFor } from '../components/materialityBands'
import './Pages.css'

const COUNTRY_COLORS: Record<string, string> = {
  China: '#e74c3c',
  Russia: '#3498db',
  Iran: '#f39c12',
  Turkey: '#9b59b6',
  'United States': '#2ecc71',
}

function ComparisonCard({ comp, onOpen }: { comp: EventComparison; onOpen: () => void }) {
  return (
    <div className="comp-card" onClick={onOpen} style={{ cursor: 'pointer' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '0.5rem' }}>
        <h3 style={{ margin: 0, fontSize: '1rem', lineHeight: 1.35, flex: 1 }}>{comp.event_name}</h3>
        <div style={{ display: 'flex', gap: '0.25rem', flexShrink: 0, marginLeft: '0.75rem' }}>
          {comp.avg_materiality != null && (
            <span style={{
              fontSize: '0.72rem', padding: '2px 7px', borderRadius: '4px',
              ...badgeStyleFor(comp.avg_materiality),
            }}>
              <Zap size={10} style={{ marginRight: 2 }} />{comp.avg_materiality.toFixed(1)}
            </span>
          )}
        </div>
      </div>

      {/* Country pills */}
      <div style={{ display: 'flex', gap: '0.375rem', flexWrap: 'wrap', marginBottom: '0.5rem' }}>
        {comp.countries.map(c => (
          <span key={c} style={{
            fontSize: '0.72rem', padding: '2px 8px', borderRadius: '4px',
            backgroundColor: `${COUNTRY_COLORS[c] || '#94a3b8'}20`,
            color: COUNTRY_COLORS[c] || '#475569',
            fontWeight: 500,
          }}>
            {c}
          </span>
        ))}
        {comp.latest_date && (
          <span style={{ fontSize: '0.72rem', color: '#94a3b8', display: 'flex', alignItems: 'center', gap: 2 }}>
            <Calendar size={10} /> {comp.latest_date}
          </span>
        )}
      </div>

      <button
        onClick={(e) => { e.stopPropagation(); onOpen() }}
        style={{
          background: 'none', border: 'none', color: '#3b82f6', cursor: 'pointer',
          fontSize: '0.82rem', padding: '0.25rem 0', display: 'flex', alignItems: 'center', gap: 4,
        }}
      >
        Compare {comp.country_count} Country Perspectives
        <ChevronRight size={14} />
      </button>
    </div>
  )
}

export default function CountryComparison() {
  const [limit, setLimit] = useState(20)
  const [selected, setSelected] = useState<EventComparison | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['country-comparison', limit],
    queryFn: () => fetchEventComparison(limit),
  })

  const comparisons = data?.comparisons || []

  return (
    <div className="page">
      <header className="page-header">
        <h1>
          <Globe size={28} style={{ marginRight: 8, verticalAlign: 'middle' }} />
          Country Comparison
        </h1>
        <p>Events tracked by multiple countries with comparative perspectives</p>
      </header>

      <PageGuide page="country-comparison" />

      {isLoading ? (
        <div className="loading">Loading comparisons...</div>
      ) : comparisons.length > 0 ? (
        <>
          <div className="ev-list">
            {comparisons.map((comp, i) => (
              <ComparisonCard key={i} comp={comp} onOpen={() => setSelected(comp)} />
            ))}
          </div>
          {comparisons.length >= limit && (
            <div style={{ textAlign: 'center', marginTop: '1.5rem' }}>
              <button
                className="ev-show-more"
                onClick={() => setLimit(prev => prev + 20)}
              >
                Show More <ChevronDown size={16} style={{ verticalAlign: 'middle' }} />
              </button>
            </div>
          )}
        </>
      ) : (
        <div className="empty-state-card">
          <Globe size={48} />
          <h3>No Multi-Country Events</h3>
          <p>No events tracked by more than one country were found.</p>
        </div>
      )}
      {selected && <ComparisonDrawer comp={selected} onClose={() => setSelected(null)} />}
    </div>
  )
}