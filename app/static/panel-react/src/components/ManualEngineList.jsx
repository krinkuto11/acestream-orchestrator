import React, { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Plus, Trash2, Server, ExternalLink } from 'lucide-react'

export function ManualEngineList({ engines, onChange, disabled }) {
    const [newEngine, setNewEngine] = useState({ host: '', port: '6878' })

    const handleAdd = () => {
        if (!newEngine.host || !newEngine.port) return

        const port = parseInt(newEngine.port)
        if (isNaN(port)) return

        const newList = [...engines, { host: newEngine.host, port: port }]
        onChange(newList)
        setNewEngine({ host: '', port: '6878' })
    }

    const handleRemove = (index) => {
        const newList = engines.filter((_, i) => i !== index)
        onChange(newList)
    }

    return (
        <div className="space-y-4">
            <div className="grid grid-cols-1 gap-3">
                {engines.map((engine, index) => (
                    <Card key={`${engine.host}-${engine.port}-${index}`} className="border-border">
                        <CardContent className="p-4 flex items-center justify-between">
                            <div className="flex items-center gap-3">
                                <div className="p-2 rounded-lg bg-secondary text-secondary-foreground">
                                    <Server className="h-4 w-4" />
                                </div>
                                <div>
                                    <p className="font-medium text-sm">{engine.host}:{engine.port}</p>
                                </div>
                            </div>
                            <Button
                                variant="ghost"
                                size="icon"
                                onClick={() => handleRemove(index)}
                                disabled={disabled}
                                className="text-muted-foreground hover:text-destructive"
                            >
                                <Trash2 className="h-4 w-4" />
                            </Button>
                        </CardContent>
                    </Card>
                ))}

                {engines.length === 0 && (
                    <div className="text-center py-8 border-2 border-dashed rounded-xl border-border">
                        <p className="text-sm text-muted-foreground">No manual engines configured yet.</p>
                    </div>
                )}
            </div>

            <Card className="border-primary/20">
                <CardContent className="p-4 space-y-4">
                    <div className="flex items-center gap-2 mb-2">
                        <Plus className="h-4 w-4 text-primary" />
                        <span className="text-sm font-semibold">Add Manual Engine</span>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="space-y-2">
                            <Label htmlFor="manual-host" className="text-xs">Host (IP or Hostname)</Label>
                            <Input
                                id="manual-host"
                                placeholder="e.g. 192.168.1.10"
                                value={newEngine.host}
                                onChange={(e) => setNewEngine({ ...newEngine, host: e.target.value })}
                                disabled={disabled}
                            />
                        </div>
                        <div className="space-y-2">
                            <Label htmlFor="manual-port" className="text-xs">HTTP Port</Label>
                            <Input
                                id="manual-port"
                                type="number"
                                placeholder="6878"
                                value={newEngine.port}
                                onChange={(e) => setNewEngine({ ...newEngine, port: e.target.value })}
                                disabled={disabled}
                            />
                        </div>
                    </div>
                    <Button
                        onClick={handleAdd}
                        disabled={disabled || !newEngine.host || !newEngine.port}
                        className="w-full flex items-center gap-2"
                    >
                        <Plus className="h-4 w-4" />
                        Add Engine to Pool
                    </Button>
                </CardContent>
            </Card>
        </div>
    )
}
