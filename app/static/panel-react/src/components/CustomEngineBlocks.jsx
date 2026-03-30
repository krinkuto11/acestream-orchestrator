import React, { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Plus, Settings2, Server, Globe, Zap, Cpu, MemoryStick, Trash2, CheckCircle2 } from 'lucide-react'
import { useNotifications } from '@/context/NotificationContext'

export function CustomEngineBlocks({ orchUrl, apiKey, fetchJSON, engineSettings, onSettingChange }) {
    const { addNotification } = useNotifications()
    const [customConfig, setCustomConfig] = useState(null)
    const [loading, setLoading] = useState(true)
    const [expanded, setExpanded] = useState(false)

    // Form state
    const [formData, setFormData] = useState({
        p2p_port: '',
        download_limit: 0,
        upload_limit: 0,
        live_cache_type: 'disk',
        buffer_time: 10,
        stats_interval: 1
    })

    useEffect(() => {
        fetchConfig()
    }, [])

    const fetchConfig = async () => {
        try {
            setLoading(true)
            const data = await fetchJSON(`${orchUrl}/api/v1/custom-variant/config`)
            setCustomConfig(data)
        } catch (err) {
            console.error('Failed to load custom config:', err)
        } finally {
            setLoading(false)
        }
    }

    const handleEdit = () => {
        if (customConfig) {
            setFormData({
                p2p_port: customConfig.p2p_port ?? '',
                download_limit: customConfig.download_limit ?? 0,
                upload_limit: customConfig.upload_limit ?? 0,
                live_cache_type: customConfig.live_cache_type ?? 'disk',
                buffer_time: customConfig.buffer_time ?? 10,
                stats_interval: customConfig.stats_interval ?? 1
            })
        } else {
            setFormData({
                p2p_port: '',
                download_limit: 0,
                upload_limit: 0,
                live_cache_type: 'disk',
                buffer_time: 10,
                stats_interval: 1
            })
        }
        setExpanded(true)
    }

    const handleSave = async () => {
        try {
            // Build updated config
            const updatedConfig = {
                ...(customConfig || {}),
                enabled: true,
                name: "Custom Engine",
                icon: "server",
                ...formData,
                p2p_port: formData.p2p_port === '' ? null : parseInt(formData.p2p_port)
            }

            await fetchJSON(`${orchUrl}/api/v1/custom-variant/config`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${apiKey}`
                },
                body: JSON.stringify(updatedConfig)
            })

            addNotification('Custom engine configuration saved.', 'success')
            setCustomConfig(updatedConfig)

            // Auto-select the custom variant
            onSettingChange('use_custom_variant', true)
            setExpanded(false)
        } catch (err) {
            addNotification(`Failed to save custom config: ${err.message}`, 'error')
        }
    }

    const handleDelete = async () => {
        try {
            if (!window.confirm('Delete custom engine configuration?')) return

            const updatedConfig = { ...customConfig, enabled: false }
            await fetchJSON(`${orchUrl}/api/v1/custom-variant/config`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${apiKey}`
                },
                body: JSON.stringify(updatedConfig)
            })

            setCustomConfig(updatedConfig)
            if (engineSettings.use_custom_variant) {
                onSettingChange('use_custom_variant', false)
            }
            setExpanded(false)
            addNotification('Custom engine deleted.', 'success')
        } catch (err) {
            addNotification(`Failed to delete config: ${err.message}`, 'error')
        }
    }

    const activateDefault = () => {
        onSettingChange('use_custom_variant', false)
        setExpanded(false)
    }

    const activateCustom = () => {
        if (customConfig?.enabled) {
            onSettingChange('use_custom_variant', true)
        } else {
            handleEdit()
        }
    }

    if (loading) {
        return <div className="animate-pulse h-32 bg-secondary/50 rounded-xl" />
    }

    return (
        <div className="flex flex-col gap-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* AceServe Default Block */}
                <Card
                    className={`relative overflow-hidden cursor-pointer transition-all border-2 ${!engineSettings.use_custom_variant ? 'border-primary shadow-md bg-primary/5' : 'border-border hover:border-primary/50'}`}
                    onClick={activateDefault}
                >
                    {!engineSettings.use_custom_variant && (
                        <div className="absolute top-3 right-3 text-primary">
                            <CheckCircle2 className="h-5 w-5" />
                        </div>
                    )}
                    <CardContent className="p-6">
                        <div className="flex items-center gap-4">
                            <div className={`p-3 rounded-xl ${!engineSettings.use_custom_variant ? 'bg-primary/20 text-primary' : 'bg-secondary text-secondary-foreground'}`}>
                                <Server className="h-6 w-6" />
                            </div>
                            <div>
                                <h3 className="font-semibold text-lg">AceServe Default</h3>
                                <p className="text-sm text-muted-foreground mt-1">Recommended baseline configuration</p>
                            </div>
                        </div>
                        <div className="mt-4 flex gap-2">
                            <span className="text-xs bg-secondary px-2 py-1 rounded-md text-secondary-foreground font-medium flex items-center gap-1">
                                <Globe className="h-3 w-3" /> Auto
                            </span>
                            <span className="text-xs bg-secondary px-2 py-1 rounded-md text-secondary-foreground font-medium flex items-center gap-1">
                                <MemoryStick className="h-3 w-3" /> Auto
                            </span>
                        </div>
                    </CardContent>
                </Card>

                {/* Custom Engine Block */}
                {customConfig?.enabled ? (
                    <Card
                        className={`relative overflow-hidden cursor-pointer transition-all border-2 ${engineSettings.use_custom_variant ? 'border-primary shadow-md bg-primary/5' : 'border-border hover:border-primary/50'}`}
                        onClick={activateCustom}
                    >
                        {engineSettings.use_custom_variant && (
                            <div className="absolute top-3 right-3 text-primary">
                                <CheckCircle2 className="h-5 w-5" />
                            </div>
                        )}
                        <CardContent className="p-6">
                            <div className="flex items-start justify-between">
                                <div className="flex items-center gap-4">
                                    <div className={`p-3 rounded-xl ${engineSettings.use_custom_variant ? 'bg-primary/20 text-primary' : 'bg-secondary text-secondary-foreground'}`}>
                                        <Cpu className="h-6 w-6" />
                                    </div>
                                    <div>
                                        <h3 className="font-semibold text-lg">Custom Engine</h3>
                                        <p className="text-sm text-muted-foreground mt-1">Specialized parameters</p>
                                    </div>
                                </div>
                                <div className="flex gap-1" onClick={e => e.stopPropagation()}>
                                    <Button variant="ghost" size="icon" onClick={handleEdit} className="h-8 w-8 text-muted-foreground hover:text-foreground">
                                        <Settings2 className="h-4 w-4" />
                                    </Button>
                                    <Button variant="ghost" size="icon" onClick={handleDelete} className="h-8 w-8 text-muted-foreground hover:text-destructive">
                                        <Trash2 className="h-4 w-4" />
                                    </Button>
                                </div>
                            </div>
                            <div className="mt-4 flex flex-wrap gap-2">
                                <span className="text-xs border border-border px-2 py-1 rounded-md text-muted-foreground font-medium flex items-center gap-1">
                                    P2P: {customConfig.p2p_port || 'Auto'}
                                </span>
                                <span className="text-xs border border-border px-2 py-1 rounded-md text-muted-foreground font-medium flex items-center gap-1">
                                    <MemoryStick className="h-3 w-3" /> {customConfig.live_cache_type}
                                </span>
                            </div>
                        </CardContent>
                    </Card>
                ) : (
                    <Card
                        className={`border-2 border-dashed ${expanded ? 'border-primary' : 'border-border hover:border-primary/50'} cursor-pointer transition-colors bg-transparent flex flex-col items-center justify-center p-6 min-h-[140px]`}
                        onClick={handleEdit}
                    >
                        <div className="h-10 w-10 rounded-full bg-secondary flex items-center justify-center mb-3 text-muted-foreground">
                            <Plus className="h-5 w-5" />
                        </div>
                        <h3 className="font-semibold">Create new Engine</h3>
                        <p className="text-xs text-muted-foreground mt-1">Configure custom ports & limits</p>
                    </Card>
                )}
            </div>

            {/* Inline Editor Form */}
            {expanded && (
                <Card className="border-primary/20 bg-primary/5">
                    <CardHeader className="pb-3">
                        <CardTitle className="text-lg">Configure Custom Engine</CardTitle>
                        <CardDescription>Set explicit AceServe parameters. Leave limits at 0 to uncap.</CardDescription>
                    </CardHeader>
                    <CardContent>
                        <div className="grid gap-6">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div className="space-y-2 flex flex-col justify-end">
                                    <div className="flex justify-between items-center">
                                        <Label>P2P Port</Label>
                                    </div>
                                    <Input
                                        type="number"
                                        placeholder="Leave open for VPN Auto-assignment"
                                        value={formData.p2p_port}
                                        onChange={e => setFormData({ ...formData, p2p_port: e.target.value })}
                                    />
                                    <p className="text-xs text-muted-foreground mt-1">Leave empty to permit your VPN provider to manage the port translation.</p>
                                </div>
                                <div className="space-y-2">
                                    <Label>Live Cache Type</Label>
                                    <Select value={formData.live_cache_type} onValueChange={v => setFormData({ ...formData, live_cache_type: v })}>
                                        <SelectTrigger>
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="disk">Disk (Recommended)</SelectItem>
                                            <SelectItem value="memory">Memory</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label>Download Limit (KB/s)</Label>
                                    <Input type="number" value={formData.download_limit} onChange={e => setFormData({ ...formData, download_limit: parseInt(e.target.value) || 0 })} />
                                </div>
                                <div className="space-y-2">
                                    <Label>Upload Limit (KB/s)</Label>
                                    <Input type="number" value={formData.upload_limit} onChange={e => setFormData({ ...formData, upload_limit: parseInt(e.target.value) || 0 })} />
                                </div>
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div className="space-y-2">
                                    <Label>Buffer Time (s)</Label>
                                    <Input type="number" value={formData.buffer_time} onChange={e => setFormData({ ...formData, buffer_time: parseInt(e.target.value) || 0 })} />
                                </div>
                                <div className="space-y-2">
                                    <Label>Stats Interval (s)</Label>
                                    <Input type="number" value={formData.stats_interval} onChange={e => setFormData({ ...formData, stats_interval: parseInt(e.target.value) || 0 })} />
                                </div>
                            </div>

                            <div className="flex justify-end gap-2 pt-2 border-t">
                                <Button variant="outline" onClick={() => setExpanded(false)}>Cancel</Button>
                                <Button onClick={handleSave}>Save Engine Configuration</Button>
                            </div>
                        </div>
                    </CardContent>
                </Card>
            )}
        </div>
    )
}
