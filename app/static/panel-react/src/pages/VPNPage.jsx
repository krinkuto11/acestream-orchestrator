import React from 'react'
import VPNStatus from '@/components/VPNStatus'
import { Card, CardContent } from '@/components/ui/card'

export function VPNPage({ vpnStatus }) {
  if (!vpnStatus.enabled) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">VPN Status</h1>
          <p className="text-muted-foreground">Monitor VPN connection and health</p>
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
      <div>
        <h1 className="text-3xl font-bold">VPN Status</h1>
        <p className="text-muted-foreground">Monitor VPN connection and health</p>
      </div>

      <VPNStatus vpnStatus={vpnStatus} />
    </div>
  )
}
