import React, { useState, useEffect } from 'react'
import EngineList from '@/components/EngineList'
import { Card, CardContent } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Progress } from '@/components/ui/progress'
import { RefreshCw, AlertCircle, CheckCircle } from 'lucide-react'

export function EnginesPage({ engines, onDeleteEngine, vpnStatus, orchUrl, fetchJSON }) {
  const [reprovisionStatus, setReprovisionStatus] = useState(null)
  const [isReprovisioning, setIsReprovisioning] = useState(false)

  // Poll for reprovision status
  useEffect(() => {
    const checkReprovisionStatus = async () => {
      try {
        const status = await fetchJSON(`${orchUrl}/custom-variant/reprovision/status`)
        setReprovisionStatus(status)
        setIsReprovisioning(status.in_progress)
      } catch (err) {
        // Ignore errors
      }
    }

    // Initial check
    checkReprovisionStatus()

    // Poll every 2 seconds
    const interval = setInterval(checkReprovisionStatus, 2000)
    return () => clearInterval(interval)
  }, [orchUrl, fetchJSON])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Engines</h1>
          <p className="text-muted-foreground mt-1">Manage and monitor AceStream engine containers</p>
        </div>
      </div>

      {/* Reprovisioning Progress */}
      {isReprovisioning && (
        <Card>
          <CardContent className="pt-6">
            <Alert>
              <RefreshCw className="h-4 w-4 animate-spin" />
              <AlertDescription>
                <div className="space-y-2">
                  <p className="font-medium">Reprovisioning in progress...</p>
                  <p className="text-sm text-muted-foreground">
                    {reprovisionStatus?.message || 'Engines are being reprovisioned with new settings.'}
                  </p>
                  <Progress value={null} className="w-full mt-2" />
                </div>
              </AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      )}

      {/* Success message after reprovisioning */}
      {!isReprovisioning && reprovisionStatus?.status === 'success' && (
        <Card>
          <CardContent className="pt-6">
            <Alert variant="default" className="border-green-500 bg-green-50 dark:bg-green-950">
              <CheckCircle className="h-4 w-4 text-green-600" />
              <AlertDescription className="text-green-800 dark:text-green-200">
                {reprovisionStatus.message}
              </AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      )}

      {/* Error message after reprovisioning */}
      {!isReprovisioning && reprovisionStatus?.status === 'error' && (
        <Card>
          <CardContent className="pt-6">
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>
                {reprovisionStatus.message}
              </AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      )}

      <EngineList
        engines={engines}
        onDeleteEngine={onDeleteEngine}
        vpnStatus={vpnStatus}
      />
    </div>
  )
}
