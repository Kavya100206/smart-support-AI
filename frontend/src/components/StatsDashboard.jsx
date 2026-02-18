import { useEffect, useState } from "react"
import { api } from "../api"

export default function StatsDashboard({ refreshKey }) {
    const [stats, setStats] = useState(null)

    useEffect(() => {
        api.getStats().then(setStats)
    }, [refreshKey])

    if (!stats) return <div className="stats-loading">Loading stats...</div>

    return (
        <div className="stats-dashboard">
            <h2>Dashboard</h2>
            <div className="stats-grid">
                <div className="stat-card">
                    <span className="stat-value">{stats.total_tickets}</span>
                    <span className="stat-label">Total Tickets</span>
                </div>
                <div className="stat-card">
                    <span className="stat-value">{stats.open_tickets}</span>
                    <span className="stat-label">Open</span>
                </div>
                <div className="stat-card">
                    <span className="stat-value">{stats.avg_tickets_per_day}</span>
                    <span className="stat-label">Avg / Day</span>
                </div>
            </div>

            <div className="breakdown-row">
                <div className="breakdown">
                    <h3>By Priority</h3>
                    {Object.entries(stats.priority_breakdown).map(([k, v]) => (
                        <div key={k} className="breakdown-item">
                            <span>{k}</span><span>{v}</span>
                        </div>
                    ))}
                </div>
                <div className="breakdown">
                    <h3>By Category</h3>
                    {Object.entries(stats.category_breakdown).map(([k, v]) => (
                        <div key={k} className="breakdown-item">
                            <span>{k}</span><span>{v}</span>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    )
}
