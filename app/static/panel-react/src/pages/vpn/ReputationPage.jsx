import React, { useState, useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ReputationHeaderBar } from './ReputationHeaderBar'
import { ReputationFilters } from './ReputationFilters'
import { ReputationTable } from './ReputationTable'
import { ReputationRightRail } from './ReputationRightRail'
import { useVpnServers } from './hooks/useVpnServers'
import { useRecentProbes } from './hooks/useRecentProbes'

export function ReputationPage({ orchUrl, apiKey, vpnNodes = [] }) {
  const [searchParams, setSearchParams] = useSearchParams()

  const [filter, setFilter] = useState({
    source: searchParams.get('source') || 'all',
    q:      searchParams.get('q')      || '',
    quarantined: searchParams.get('quarantined') || 'include',
    sort:   searchParams.get('sort')   || 'score',
    dir:    searchParams.get('dir')    || 'desc',
    category: searchParams.get('category') || '',
  })

  const handleFilterChange = useCallback(f => {
    setFilter(f)
    const p = {}
    if (f.source && f.source !== 'all') p.source = f.source
    if (f.q) p.q = f.q
    if (f.quarantined && f.quarantined !== 'include') p.quarantined = f.quarantined
    if (f.sort && f.sort !== 'score') p.sort = f.sort
    if (f.dir && f.dir !== 'desc') p.dir = f.dir
    if (f.category) p.category = f.category
    setSearchParams(p, { replace: true })
  }, [setSearchParams])

  const handleSort = useCallback(sortKey => {
    setFilter(prev => {
      const newDir = prev.sort === sortKey && prev.dir === 'desc' ? 'asc' : 'desc'
      const next = { ...prev, sort: sortKey, dir: newDir }
      handleFilterChange(next)
      return next
    })
  }, [handleFilterChange])

  const { items, nextCursor, totalMatched, stats, loading, loadMore, refetch } = useVpnServers({
    orchUrl, apiKey, filter,
  })

  const { probes } = useRecentProbes({ orchUrl, apiKey })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      <ReputationHeaderBar
        stats={stats}
        orchUrl={orchUrl}
        apiKey={apiKey}
        onRefresh={refetch}
      />
      <ReputationFilters
        filter={filter}
        onChange={handleFilterChange}
        totalMatched={totalMatched}
        totalAll={Object.values(stats.by_source || {}).reduce((a, b) => a + b, 0)}
      />

      <div style={{ display: 'flex', flex: 1, overflow: 'hidden', minHeight: 0 }}>
        <ReputationTable
          items={items}
          loading={loading}
          sort={filter.sort}
          dir={filter.dir}
          onSort={handleSort}
          onLoadMore={loadMore}
          hasMore={!!nextCursor}
          orchUrl={orchUrl}
          apiKey={apiKey}
          onAction={refetch}
        />
        <ReputationRightRail
          vpnNodes={vpnNodes}
          recentProbes={probes}
        />
      </div>
    </div>
  )
}
