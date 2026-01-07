import React, { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Textarea } from '@/components/ui/textarea'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Button } from '@/components/ui/button'
import { Alert, AlertTitle, AlertDescription } from '@/components/ui/alert'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { toast } from 'sonner'
import { 
  AlertCircle, 
  CheckCircle2, 
  AlertTriangle, 
  ChevronDown, 
  ChevronUp, 
  Copy,
  Loader2,
  PlayCircle
} from 'lucide-react'

export function SettingsPage({
  apiKey,
  setApiKey,
  refreshInterval,
  setRefreshInterval,
  maxEventsDisplay,
  setMaxEventsDisplay
}) {
  const [testAceIds, setTestAceIds] = useState('')
  const [diagnosticsRunning, setDiagnosticsRunning] = useState(false)
  const [diagnosticsReport, setDiagnosticsReport] = useState(null)
  const [expandedTests, setExpandedTests] = useState(new Set())

  const toggleTestExpanded = (testName) => {
    setExpandedTests(prev => {
      const newSet = new Set(prev)
      if (newSet.has(testName)) {
        newSet.delete(testName)
      } else {
        newSet.add(testName)
      }
      return newSet
    })
  }

  const runDiagnostics = async () => {
    setDiagnosticsRunning(true)
    setDiagnosticsReport(null)
    
    try {
      const aceIds = testAceIds
        .split(/[\n,]/)
        .map(id => id.trim())
        .filter(id => id.length > 0)
      
      const orchUrl = window.location.origin
      const response = await fetch(`${orchUrl}/diagnostics/run`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          test_ace_ids: aceIds
        })
      })
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }
      
      const report = await response.json()
      setDiagnosticsReport(report)
      
      if (report.overall_status === 'passed') {
        toast.success('All diagnostic tests passed!')
      } else if (report.overall_status === 'warning') {
        toast.warning('Diagnostics completed with warnings')
      } else {
        toast.error('Some diagnostic tests failed')
      }
    } catch (error) {
      toast.error(`Diagnostics failed: ${error.message}`)
      console.error('Diagnostics error:', error)
    } finally {
      setDiagnosticsRunning(false)
    }
  }

  const copyReportToClipboard = () => {
    if (!diagnosticsReport) return
    
    const text = JSON.stringify(diagnosticsReport, null, 2)
    navigator.clipboard.writeText(text)
      .then(() => toast.success('Diagnostic report copied to clipboard'))
      .catch(() => toast.error('Failed to copy to clipboard'))
  }

  const getStatusIcon = (status) => {
    switch (status) {
      case 'passed':
        return <CheckCircle2 className="h-5 w-5 text-green-600 dark:text-green-400" />
      case 'failed':
        return <AlertCircle className="h-5 w-5 text-red-600 dark:text-red-400" />
      case 'warning':
        return <AlertTriangle className="h-5 w-5 text-yellow-600 dark:text-yellow-400" />
      default:
        return <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
    }
  }

  const getStatusVariant = (status) => {
    switch (status) {
      case 'passed':
        return 'success'
      case 'failed':
        return 'destructive'
      case 'warning':
        return 'warning'
      default:
        return 'default'
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
          <p className="text-muted-foreground mt-1">Configure dashboard connection and preferences</p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Connection Settings</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="api-key">API Key</Label>
            <Input
              id="api-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Enter your API key"
            />
            <p className="text-xs text-muted-foreground">
              Required for protected endpoints (provisioning, deletion, etc.)
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Display Settings</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="refresh-interval">Auto Refresh Interval</Label>
            <Select 
              value={refreshInterval.toString()} 
              onValueChange={(val) => setRefreshInterval(Number(val))}
            >
              <SelectTrigger id="refresh-interval">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="2000">2 seconds</SelectItem>
                <SelectItem value="5000">5 seconds</SelectItem>
                <SelectItem value="10000">10 seconds</SelectItem>
                <SelectItem value="30000">30 seconds</SelectItem>
                <SelectItem value="60000">1 minute</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              How often the dashboard refreshes data from the server
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="max-events">Event Log Display Limit</Label>
            <Select 
              value={maxEventsDisplay.toString()} 
              onValueChange={(val) => setMaxEventsDisplay(Number(val))}
            >
              <SelectTrigger id="max-events">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="50">50 events</SelectItem>
                <SelectItem value="100">100 events</SelectItem>
                <SelectItem value="200">200 events</SelectItem>
                <SelectItem value="500">500 events</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Maximum number of events to display in the Event Log page
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>AceStream Proxy Diagnostics</CardTitle>
          <CardDescription>
            Test proxy connectivity to AceStream engines and debug connection issues
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="test-ace-ids">Test AceStream IDs</Label>
            <Textarea
              id="test-ace-ids"
              value={testAceIds}
              onChange={(e) => setTestAceIds(e.target.value)}
              placeholder="Enter AceStream IDs to test (one per line or comma-separated)&#10;Example:&#10;0000000000000000000000000000000000000000&#10;332d8fdeb9c51385230b8c9448e047ccc4a6355d"
              rows={4}
              className="font-mono text-xs"
            />
            <p className="text-xs text-muted-foreground">
              Enter AceStream content IDs or infohashes to test. Leave empty to run basic diagnostics.
            </p>
          </div>

          <div className="flex gap-2">
            <Button
              onClick={runDiagnostics}
              disabled={diagnosticsRunning}
              className="flex items-center gap-2"
            >
              {diagnosticsRunning ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Running Diagnostics...
                </>
              ) : (
                <>
                  <PlayCircle className="h-4 w-4" />
                  Run Diagnostics
                </>
              )}
            </Button>

            {diagnosticsReport && (
              <Button
                onClick={copyReportToClipboard}
                variant="outline"
                className="flex items-center gap-2"
              >
                <Copy className="h-4 w-4" />
                Copy Report
              </Button>
            )}
          </div>

          {diagnosticsReport && (
            <div className="space-y-4 mt-6">
              <Alert variant={getStatusVariant(diagnosticsReport.overall_status)}>
                <div className="flex items-start gap-3">
                  {getStatusIcon(diagnosticsReport.overall_status)}
                  <div className="flex-1">
                    <AlertTitle>
                      Diagnostics {diagnosticsReport.overall_status === 'passed' ? 'Passed' : 
                                  diagnosticsReport.overall_status === 'warning' ? 'Completed with Warnings' : 
                                  'Failed'}
                    </AlertTitle>
                    <AlertDescription>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-2 text-sm">
                        <div>Total: <strong>{diagnosticsReport.summary.total_tests}</strong></div>
                        <div className="text-green-700 dark:text-green-300">
                          Passed: <strong>{diagnosticsReport.summary.passed}</strong>
                        </div>
                        <div className="text-red-700 dark:text-red-300">
                          Failed: <strong>{diagnosticsReport.summary.failed}</strong>
                        </div>
                        <div className="text-yellow-700 dark:text-yellow-300">
                          Warnings: <strong>{diagnosticsReport.summary.warnings}</strong>
                        </div>
                      </div>
                      <div className="mt-2 text-xs">
                        Duration: {diagnosticsReport.summary.duration?.toFixed(2)}s
                      </div>
                    </AlertDescription>
                  </div>
                </div>
              </Alert>

              <div className="space-y-2">
                <h3 className="text-sm font-semibold">Test Results</h3>
                {diagnosticsReport.tests.map((test, idx) => (
                  <Collapsible
                    key={idx}
                    open={expandedTests.has(test.name)}
                    onOpenChange={() => toggleTestExpanded(test.name)}
                  >
                    <Card className={`border-l-4 ${
                      test.status === 'passed' ? 'border-l-green-500' :
                      test.status === 'failed' ? 'border-l-red-500' :
                      test.status === 'warning' ? 'border-l-yellow-500' :
                      'border-l-gray-500'
                    }`}>
                      <CollapsibleTrigger className="w-full">
                        <CardHeader className="cursor-pointer hover:bg-muted/50 transition-colors">
                          <div className="flex items-center justify-between">
                            <div className="flex items-center gap-3 flex-1 text-left">
                              {getStatusIcon(test.status)}
                              <div>
                                <CardTitle className="text-sm">{test.description}</CardTitle>
                                {test.message && (
                                  <p className="text-xs text-muted-foreground mt-1">{test.message}</p>
                                )}
                              </div>
                            </div>
                            <div className="flex items-center gap-2">
                              {test.duration && (
                                <span className="text-xs text-muted-foreground">
                                  {test.duration.toFixed(2)}s
                                </span>
                              )}
                              {expandedTests.has(test.name) ? (
                                <ChevronUp className="h-4 w-4" />
                              ) : (
                                <ChevronDown className="h-4 w-4" />
                              )}
                            </div>
                          </div>
                        </CardHeader>
                      </CollapsibleTrigger>
                      
                      <CollapsibleContent>
                        <CardContent className="pt-0">
                          {test.error && (
                            <Alert variant="destructive" className="mb-4">
                              <AlertCircle className="h-4 w-4" />
                              <AlertTitle>Error</AlertTitle>
                              <AlertDescription className="font-mono text-xs">
                                {test.error}
                              </AlertDescription>
                            </Alert>
                          )}
                          
                          {test.details && Object.keys(test.details).length > 0 && (
                            <div className="space-y-2">
                              <h4 className="text-xs font-semibold text-muted-foreground">Details</h4>
                              <pre className="text-xs bg-muted p-3 rounded-md overflow-x-auto max-h-96">
                                {JSON.stringify(test.details, null, 2)}
                              </pre>
                            </div>
                          )}
                        </CardContent>
                      </CollapsibleContent>
                    </Card>
                  </Collapsible>
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
