import React, { useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Checkbox } from '@/components/ui/checkbox'
import { Label } from '@/components/ui/label'
import { Download, Upload, FileArchive, CheckCircle, AlertTriangle, Info } from 'lucide-react'
import { toast } from 'sonner'

export function BackupSettings({ apiKey, orchUrl }) {
  const [importing, setImporting] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [importOptions, setImportOptions] = useState({
    custom_variant: true,
    templates: true,
    proxy: true,
    loop_detection: true,
    engine: true,
  })
  const [importResult, setImportResult] = useState(null)

  const handleExport = async () => {
    try {
      setExporting(true)
      
      const headers = {}
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }
      
      const response = await fetch(`${orchUrl}/settings/export`, {
        method: 'GET',
        headers
      })
      
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({ detail: response.statusText }))
        throw new Error(errorData.detail || `Export failed: ${response.status}`)
      }
      
      // Download the ZIP file
      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      
      // Extract filename from Content-Disposition header if available
      const contentDisposition = response.headers.get('Content-Disposition')
      let filename = 'orchestrator_settings.zip'
      if (contentDisposition) {
        const filenameMatch = contentDisposition.match(/filename="(.+)"/)
        if (filenameMatch) {
          filename = filenameMatch[1]
        }
      }
      
      a.download = filename
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(url)
      document.body.removeChild(a)
      
      toast.success('Settings exported successfully')
    } catch (err) {
      console.error('Export error:', err)
      toast.error(`Failed to export settings: ${err.message}`)
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
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }
      
      // Build query params for import options
      const params = new URLSearchParams({
        import_custom_variant: importOptions.custom_variant,
        import_templates: importOptions.templates,
        import_proxy: importOptions.proxy,
        import_loop_detection: importOptions.loop_detection,
        import_engine: importOptions.engine,
      })
      
      const response = await fetch(`${orchUrl}/settings/import?${params}`, {
        method: 'POST',
        headers,
        body: file
      })
      
      const result = await response.json()
      
      if (!response.ok) {
        throw new Error(result.detail || `Import failed: ${response.status}`)
      }
      
      setImportResult(result)
      
      // Show summary of what was imported
      const imported = result.imported
      const messages = []
      if (imported.custom_variant) messages.push('Custom engine config')
      if (imported.templates > 0) messages.push(`${imported.templates} template(s)`)
      if (imported.active_template) messages.push('Active template')
      if (imported.proxy) messages.push('Proxy settings')
      if (imported.loop_detection) messages.push('Loop detection settings')
      if (imported.engine) messages.push('Engine settings')
      
      if (messages.length > 0) {
        toast.success(`Imported: ${messages.join(', ')}`)
      }
      
      if (imported.errors && imported.errors.length > 0) {
        toast.warning(`Import completed with ${imported.errors.length} error(s)`)
      }
      
      // Clear the file input
      event.target.value = ''
    } catch (err) {
      console.error('Import error:', err)
      toast.error(`Failed to import settings: ${err.message}`)
      setImportResult(null)
    } finally {
      setImporting(false)
    }
  }

  const toggleImportOption = (key) => {
    setImportOptions(prev => ({
      ...prev,
      [key]: !prev[key]
    }))
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FileArchive className="h-5 w-5" />
            Backup & Restore
          </CardTitle>
          <CardDescription>
            Export and import your orchestrator settings including custom engine configurations, 
            templates, proxy settings, and loop detection settings.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {/* Export Section */}
          <div className="space-y-4">
            <div>
              <h3 className="text-lg font-semibold mb-2">Export Settings</h3>
              <p className="text-sm text-muted-foreground mb-4">
                Download all your current settings as a ZIP file. This includes:
              </p>
              <ul className="text-sm text-muted-foreground space-y-1 ml-4 mb-4">
                <li className="flex items-center gap-2">
                  <CheckCircle className="h-3 w-3" />
                  Custom engine variant configuration
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle className="h-3 w-3" />
                  All custom engine templates (up to 10 slots)
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle className="h-3 w-3" />
                  Active template selection
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle className="h-3 w-3" />
                  Engine settings (min/max replicas, auto-delete)
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle className="h-3 w-3" />
                  Proxy configuration
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle className="h-3 w-3" />
                  Loop detection settings
                </li>
              </ul>
            </div>
            
            <Button
              onClick={handleExport}
              disabled={exporting}
              className="w-full sm:w-auto"
            >
              <Download className="h-4 w-4 mr-2" />
              {exporting ? 'Exporting...' : 'Export Settings'}
            </Button>
          </div>

          <div className="border-t pt-6">
            {/* Import Section */}
            <div className="space-y-4">
              <div>
                <h3 className="text-lg font-semibold mb-2">Import Settings</h3>
                <p className="text-sm text-muted-foreground mb-4">
                  Restore settings from a previously exported ZIP file. Choose which settings to import:
                </p>
              </div>
              
              {/* Import Options */}
              <div className="space-y-3 p-4 border rounded-lg bg-muted/50">
                <div className="flex items-center space-x-2">
                  <Checkbox
                    id="import-custom-variant"
                    checked={importOptions.custom_variant}
                    onCheckedChange={() => toggleImportOption('custom_variant')}
                  />
                  <Label
                    htmlFor="import-custom-variant"
                    className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 cursor-pointer"
                  >
                    Custom Engine Variant Configuration
                  </Label>
                </div>
                
                <div className="flex items-center space-x-2">
                  <Checkbox
                    id="import-templates"
                    checked={importOptions.templates}
                    onCheckedChange={() => toggleImportOption('templates')}
                  />
                  <Label
                    htmlFor="import-templates"
                    className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 cursor-pointer"
                  >
                    Custom Engine Templates & Active Template
                  </Label>
                </div>
                
                <div className="flex items-center space-x-2">
                  <Checkbox
                    id="import-engine"
                    checked={importOptions.engine}
                    onCheckedChange={() => toggleImportOption('engine')}
                  />
                  <Label
                    htmlFor="import-engine"
                    className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 cursor-pointer"
                  >
                    Engine Settings (Min/Max Replicas, Auto-Delete)
                  </Label>
                </div>
                
                <div className="flex items-center space-x-2">
                  <Checkbox
                    id="import-proxy"
                    checked={importOptions.proxy}
                    onCheckedChange={() => toggleImportOption('proxy')}
                  />
                  <Label
                    htmlFor="import-proxy"
                    className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 cursor-pointer"
                  >
                    Proxy Settings
                  </Label>
                </div>
                
                <div className="flex items-center space-x-2">
                  <Checkbox
                    id="import-loop-detection"
                    checked={importOptions.loop_detection}
                    onCheckedChange={() => toggleImportOption('loop_detection')}
                  />
                  <Label
                    htmlFor="import-loop-detection"
                    className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70 cursor-pointer"
                  >
                    Loop Detection Settings
                  </Label>
                </div>
              </div>
              
              {/* File Upload */}
              <div className="flex items-center gap-3">
                <Button
                  onClick={() => document.getElementById('import-file-input').click()}
                  disabled={importing}
                  className="w-full sm:w-auto"
                >
                  <Upload className="h-4 w-4 mr-2" />
                  {importing ? 'Importing...' : 'Import Settings'}
                </Button>
                <input
                  id="import-file-input"
                  type="file"
                  accept=".zip"
                  onChange={handleImport}
                  className="hidden"
                />
              </div>
              
              {/* Import Result */}
              {importResult && (
                <Alert variant={importResult.imported.errors?.length > 0 ? "warning" : "default"}>
                  {importResult.imported.errors?.length > 0 ? (
                    <AlertTriangle className="h-4 w-4" />
                  ) : (
                    <CheckCircle className="h-4 w-4" />
                  )}
                  <AlertTitle>
                    {importResult.imported.errors?.length > 0 
                      ? 'Import Completed with Warnings' 
                      : 'Import Successful'}
                  </AlertTitle>
                  <AlertDescription className="space-y-2">
                    <div className="flex flex-wrap gap-2 mt-2">
                      {importResult.imported.custom_variant && (
                        <Badge variant="success">Custom Variant</Badge>
                      )}
                      {importResult.imported.templates > 0 && (
                        <Badge variant="success">{importResult.imported.templates} Template(s)</Badge>
                      )}
                      {importResult.imported.active_template && (
                        <Badge variant="success">Active Template</Badge>
                      )}
                      {importResult.imported.engine && (
                        <Badge variant="success">Engine Settings</Badge>
                      )}
                      {importResult.imported.proxy && (
                        <Badge variant="success">Proxy Settings</Badge>
                      )}
                      {importResult.imported.loop_detection && (
                        <Badge variant="success">Loop Detection</Badge>
                      )}
                    </div>
                    {importResult.imported.errors?.length > 0 && (
                      <div className="mt-3 space-y-1">
                        <p className="text-sm font-medium">Errors:</p>
                        {importResult.imported.errors.map((error, index) => (
                          <p key={index} className="text-sm text-muted-foreground">• {error}</p>
                        ))}
                      </div>
                    )}
                  </AlertDescription>
                </Alert>
              )}
            </div>
          </div>

          {/* Warning Note */}
          <Alert>
            <Info className="h-4 w-4" />
            <AlertTitle>Important Notes</AlertTitle>
            <AlertDescription className="text-sm space-y-2">
              <p>
                • Importing settings will overwrite your current configuration
              </p>
              <p>
                • After importing custom engine settings or templates, you may need to reprovision engines for changes to take effect
              </p>
              <p>
                • It's recommended to export your current settings before importing
              </p>
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    </div>
  )
}
