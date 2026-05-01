import React, { useState } from 'react'
import { useNotifications } from '@/context/NotificationContext'

export function BackupSettings({ apiKey, orchUrl }) {
  const { addNotification } = useNotifications()
  const [importing, setImporting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [importOptions, setImportOptions] = useState({
    engine_config: true,
    proxy: true,
    engine: true,
  })
  const [importResult, setImportResult] = useState(null)

  const handleExport = async () => {
    try {
      setExporting(true)
      const headers = {}
      if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`
      const response = await fetch(`${orchUrl}/api/v1/settings/export`, { method: 'GET', headers })
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: response.statusText }))
        throw new Error(errorData.detail || `Export failed: ${response.status}`)
      }
      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      const contentDisposition = response.headers.get('Content-Disposition')
      let filename = 'orchestrator_settings.zip'
      if (contentDisposition) {
        const m = contentDisposition.match(/filename="(.+)"/)
        if (m) filename = m[1]
      }
      a.download = filename
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(url)
      document.body.removeChild(a)
      addNotification('Settings exported successfully', 'success')
    } catch (err) {
      addNotification(`Failed to export settings: ${err.message}`, 'error')
    } finally {
      setExporting(false)
    }
  }

  const handleImport = async (event) => {
    const file = event.target.files[0]
    if (!file) return
    try {
      setImporting(true)
      setImportResult(null)
      const headers = {}
      if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`
      const params = new URLSearchParams({
        import_engine_config: importOptions.engine_config,
        import_proxy: importOptions.proxy,
        import_engine: importOptions.engine,
      })
      const response = await fetch(`${orchUrl}/api/v1/settings/import?${params}`, {
        method: 'POST', headers, body: file,
      })
      const result = await response.json()
      if (!response.ok) throw new Error(result.detail || `Import failed: ${response.status}`)
      setImportResult(result)
      const imported = result.imported
      const messages = []
      if (imported.engine_config) messages.push('Global engine config')
      if (imported.proxy) messages.push('Proxy settings')
      if (imported.engine) messages.push('Engine settings')
      if (messages.length > 0) addNotification(`Imported: ${messages.join(', ')}`, 'success')
      if (imported.errors && imported.errors.length > 0) addNotification(`Import completed with ${imported.errors.length} error(s)`, 'warning')
      event.target.value = ''
    } catch (err) {
      addNotification(`Failed to import settings: ${err.message}`, 'error')
      setImportResult(null)
    } finally {
      setImporting(false)
    }
  }

  const toggleImportOption = (key) => {
    setImportOptions(prev => ({ ...prev, [key]: !prev[key] }))
  }

  const btnStyle = {
    background: 'none', border: '1px solid var(--line)', color: 'var(--fg-1)',
    padding: '6px 14px', fontFamily: 'var(--font-mono)', fontSize: 11,
    cursor: 'pointer',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ background: 'var(--bg-1)', border: '1px solid var(--line-soft)' }}>
        <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)' }}>
          <span className="label">BACKUP &amp; RESTORE</span>
          <div style={{ fontSize: 10, color: 'var(--fg-2)', marginTop: 2 }}>
            Export and import orchestrator settings including engine configuration, runtime settings, and proxy settings.
          </div>
        </div>
        <div style={{ padding: '14px' }}>

          {/* Info banner */}
          <div style={{ background: 'var(--bg-2)', border: '1px solid var(--line)', padding: '10px 14px', marginBottom: 20, fontSize: 10, color: 'var(--fg-2)' }}>
            ⓘ Export and import operations execute immediately and do not participate in the global unsaved changes workflow.
          </div>

          {/* Export */}
          <div style={{ marginBottom: 24 }}>
            <div style={{ fontSize: 12, color: 'var(--fg-0)', fontWeight: 600, fontFamily: 'var(--font-mono)', marginBottom: 6 }}>Export Settings</div>
            <div style={{ fontSize: 10, color: 'var(--fg-2)', marginBottom: 10 }}>Download all current settings as a ZIP file. Includes:</div>
            <div style={{ fontSize: 10, color: 'var(--fg-3)', display: 'flex', flexDirection: 'column', gap: 3, marginBottom: 14, paddingLeft: 8 }}>
              <span>✓ Global engine configuration</span>
              <span>✓ Engine settings (min/max replicas, auto-delete)</span>
              <span>✓ Proxy configuration</span>
            </div>
            <button onClick={handleExport} disabled={exporting} style={{ ...btnStyle, opacity: exporting ? 0.5 : 1 }}>
              {exporting ? '⟳ EXPORTING...' : '↓ EXPORT SETTINGS'}
            </button>
          </div>

          <div style={{ borderTop: '1px solid var(--line-soft)', paddingTop: 20 }}>
            <div style={{ fontSize: 12, color: 'var(--fg-0)', fontWeight: 600, fontFamily: 'var(--font-mono)', marginBottom: 6 }}>Import Settings</div>
            <div style={{ fontSize: 10, color: 'var(--fg-2)', marginBottom: 12 }}>Restore settings from a previously exported ZIP file. Choose which settings to import:</div>

            {/* Import options */}
            <div style={{ background: 'var(--bg-2)', border: '1px solid var(--line)', padding: '12px 14px', marginBottom: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
              {[
                { key: 'engine_config', label: 'Global Engine Configuration' },
                { key: 'engine', label: 'Engine Settings (Min/Max Replicas, Auto-Delete)' },
                { key: 'proxy', label: 'Proxy Settings' },
              ].map(({ key, label }) => (
                <label key={key} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 11, color: 'var(--fg-1)', fontFamily: 'var(--font-mono)' }}>
                  <input
                    type="checkbox"
                    checked={importOptions[key]}
                    onChange={() => toggleImportOption(key)}
                    style={{ accentColor: 'var(--acc-green)', cursor: 'pointer' }}
                  />
                  {label}
                </label>
              ))}
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <button
                onClick={() => document.getElementById('import-file-input').click()}
                disabled={importing}
                style={{ ...btnStyle, opacity: importing ? 0.5 : 1 }}
              >
                {importing ? '⟳ IMPORTING...' : '↑ IMPORT SETTINGS'}
              </button>
              <input id="import-file-input" type="file" accept=".zip" onChange={handleImport} style={{ display: 'none' }}/>
            </div>

            {/* Import result */}
            {importResult && (
              <div style={{
                marginTop: 14,
                background: importResult.imported.errors?.length > 0 ? 'var(--acc-amber-bg)' : 'var(--bg-2)',
                border: `1px solid ${importResult.imported.errors?.length > 0 ? 'var(--acc-amber-dim)' : 'var(--acc-green-dim)'}`,
                padding: '10px 14px',
                fontSize: 10,
                color: 'var(--fg-1)',
              }}>
                <div style={{ fontWeight: 600, marginBottom: 6, color: importResult.imported.errors?.length > 0 ? 'var(--acc-amber)' : 'var(--acc-green)' }}>
                  {importResult.imported.errors?.length > 0 ? '⚠ Import Completed with Warnings' : '✓ Import Successful'}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {importResult.imported.engine_config && <span className="tag tag-green" style={{ fontSize: 9 }}>Engine Config</span>}
                  {importResult.imported.engine && <span className="tag tag-green" style={{ fontSize: 9 }}>Engine Settings</span>}
                  {importResult.imported.proxy && <span className="tag tag-green" style={{ fontSize: 9 }}>Proxy Settings</span>}
                </div>
                {importResult.imported.errors?.length > 0 && (
                  <div style={{ marginTop: 8 }}>
                    {importResult.imported.errors.map((err, i) => (
                      <div key={i} style={{ color: 'var(--fg-2)' }}>• {err}</div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Warning */}
          <div style={{ background: 'var(--bg-2)', border: '1px solid var(--line)', padding: '10px 14px', marginTop: 20, fontSize: 10, color: 'var(--fg-2)' }}>
            ⓘ Importing settings will overwrite your current configuration. After importing engine settings/configuration, you may need to reprovision engines. Export your current settings before importing.
          </div>
        </div>
      </div>
    </div>
  )
}
