import React, { useState, useEffect } from 'react'
import EngineList from '@/components/EngineList'
import { Card, CardContent } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Progress } from '@/components/ui/progress'
import { RefreshCw, AlertCircle, CheckCircle } from 'lucide-react'

export function EnginesPage({ engines, onDeleteEngine, vpnStatus, orchUrl, fetchJSON }) {
  const [reprovisionStatus, setReprovisionStatus] = useState(null)
  const [isReprovisioning, setIsReprovisioning] = useState(false)
  const [showSuccessMessage, setShowSuccessMessage] = useState(false)
  const [showErrorMessage, setShowErrorMessage] = useState(false)

  // Poll for reprovision status
  useEffect(() => {
    const checkReprovisionStatus = async () => {
      try {
        const status = await fetchJSON(`${orchUrl}/custom-variant/reprovision/status`)
        const wasReprovisioning = isReprovisioning
        
        setReprovisionStatus(status)
        setIsReprovisioning(status.in_progress)
        
        // When reprovisioning completes, show success/error message briefly
        if (wasReprovisioning && !status.in_progress) {
          if (status.status === 'success') {
            setShowSuccessMessage(true)
            // Auto-dismiss success message after 10 seconds
            setTimeout(() => setShowSuccessMessage(false), 10000)
          } else if (status.status === 'error') {
            setShowErrorMessage(true)
            // Auto-dismiss error message after 15 seconds
            setTimeout(() => setShowErrorMessage(false), 15000)
          }
        }
      } catch (err) {
        // Ignore errors
      }
    }

    // Initial check
    checkReprovisionStatus()

    // Poll every 2 seconds
    const interval = setInterval(checkReprovisionStatus, 2000)
    return () => clearInterval(interval)
  }, [orchUrl, fetchJSON, isReprovisioning])

  // Clear success/error message when component unmounts (user navigates away)
  useEffect(() => {
    return () => {
      // Clear the status when leaving the page
      setReprovisionStatus(null)
      setIsReprovisioning(false)
      setShowSuccessMessage(false)
      setShowErrorMessage(false)
    }
  }, [])

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
                  {(() => {
                    // Calculate progress percentage based on current phase and counts
                    let progress = 0
                    if (reprovisionStatus) {
                      const { current_phase, engines_stopped = 0, total_engines = 0 } = reprovisionStatus
                      
                      if (current_phase === 'stopping' && total_engines > 0) {
                        // Stopping phase: 0-40% of progress
                        progress = Math.round((engines_stopped / total_engines) * 40)
                      } else if (current_phase === 'cleaning') {
                        // Cleaning phase: 40-50% of progress
                        progress = 45
                      } else if (current_phase === 'provisioning') {
                        // Provisioning phase: 50-100% of progress
                        progress = 50 + Math.round((engines_stopped / Math.max(total_engines, 1)) * 50)
                      } else if (current_phase === 'complete') {
                        progress = 100
                      }
                    }
                    return <Progress value={progress} className="w-full mt-2" />
                  })()}
                </div>
              </AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      )}

      {/* Success message after reprovisioning */}
      {!isReprovisioning && showSuccessMessage && reprovisionStatus?.status === 'success' && (
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
      {!isReprovisioning && showErrorMessage && reprovisionStatus?.status === 'error' && (
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
        orchUrl={orchUrl}
      />
    </div>
  )
}
