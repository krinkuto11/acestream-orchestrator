import React from 'react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

export function EngineConfiguration({ engineSettings, onSettingChange, disabled = false }) {
  const updateNumber = (key, value, fallback = 0) => {
    const parsed = Number.parseInt(String(value), 10)
    onSettingChange(key, Number.isFinite(parsed) ? parsed : fallback)
  }

  return (
    <div className="grid gap-5">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>Live Cache Type</Label>
          <Select
            value={String(engineSettings.live_cache_type || 'memory')}
            onValueChange={(value) => onSettingChange('live_cache_type', value)}
            disabled={disabled}
          >
            <SelectTrigger>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="memory">Memory (Recommended)</SelectItem>
              <SelectItem value="disk">Disk</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <div className="space-y-2">
          <Label>Memory Limit</Label>
          <Input
            type="text"
            placeholder="e.g. 512m or 1g"
            value={engineSettings.memory_limit || ''}
            onChange={(e) => onSettingChange('memory_limit', e.target.value || null)}
            disabled={disabled}
          />
          <p className="text-xs text-muted-foreground">Optional container memory limit; leave empty for default.</p>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>Download Limit (KB/s)</Label>
          <Input
            type="number"
            min="0"
            value={engineSettings.total_max_download_rate ?? 0}
            onChange={(e) => updateNumber('total_max_download_rate', e.target.value, 0)}
            disabled={disabled}
          />
        </div>

        <div className="space-y-2">
          <Label>Upload Limit (KB/s)</Label>
          <Input
            type="number"
            min="0"
            value={engineSettings.total_max_upload_rate ?? 0}
            onChange={(e) => updateNumber('total_max_upload_rate', e.target.value, 0)}
            disabled={disabled}
          />
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label>Buffer Time (s)</Label>
          <Input
            type="number"
            min="0"
            value={engineSettings.buffer_time ?? 30}
            onChange={(e) => updateNumber('buffer_time', e.target.value, 30)}
            disabled={disabled}
          />
        </div>

        <div className="space-y-2">
          <Label>Max Peers</Label>
          <Input
            type="number"
            min="0"
            value={engineSettings.max_peers ?? 50}
            onChange={(e) => updateNumber('max_peers', e.target.value, 50)}
            disabled={disabled}
          />
          <p className="text-xs text-muted-foreground">0 = unlimited</p>
        </div>
      </div>
    </div>
  )
}

// Compatibility export while call sites migrate.
export const CustomEngineBlocks = EngineConfiguration
