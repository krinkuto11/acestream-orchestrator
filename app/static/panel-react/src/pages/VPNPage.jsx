import React from 'react'
import VPNStatus from '@/components/VPNStatus'
import { Card, CardContent } from '@/components/ui/card'

export function VPNPage({ vpnStatus }) {
  if (!vpnStatus.enabled) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-3xl font-bold tracking-tight">VPN Status</h1>
            <p className="text-muted-foreground mt-1">Monitor VPN connection and health</p>
          </div>
        </div>

        <Card>
          <CardContent className="pt-6">
            <p className="text-muted-foreground">VPN monitoring is not enabled</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">VPN Status</h1>
          <p className="text-muted-foreground mt-1">Monitor VPN connection and health</p>
        </div>
      </div>

      <VPNStatus vpnStatus={vpnStatus} />
    </div>
  )
}
