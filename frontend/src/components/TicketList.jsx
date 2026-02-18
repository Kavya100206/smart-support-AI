import { useState, useEffect } from "react"
import { api } from "../api"
import TicketCard from "./TicketCard"

const CATEGORIES = ["billing", "technical", "account", "general"]
const PRIORITIES = ["low", "medium", "high", "critical"]
const STATUSES = ["open", "in_progress", "resolved", "closed"]

export default function TicketList({ tickets, setTickets }) {
    const [filters, setFilters] = useState({ category: "", priority: "", status: "", search: "" })

    useEffect(() => {
        const params = {}
        if (filters.category) params.category = filters.category
        if (filters.priority) params.priority = filters.priority
        if (filters.status) params.status = filters.status
        if (filters.search) params.search = filters.search
        api.getTickets(params).then(setTickets)
    }, [filters])

    const handleUpdated = (updated) => {
        setTickets(prev => prev.map(t => t.id === updated.id ? updated : t))
    }

    const setFilter = (key, value) => setFilters(prev => ({ ...prev, [key]: value }))

    return (
        <div className="ticket-list-section">
            <h2>All Tickets</h2>

            <div className="filters">
                <input
                    type="text"
                    placeholder="Search tickets..."
                    value={filters.search}
                    onChange={e => setFilter("search", e.target.value)}
                />
                <select value={filters.category} onChange={e => setFilter("category", e.target.value)}>
                    <option value="">All Categories</option>
                    {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
                <select value={filters.priority} onChange={e => setFilter("priority", e.target.value)}>
                    <option value="">All Priorities</option>
                    {PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
                <select value={filters.status} onChange={e => setFilter("status", e.target.value)}>
                    <option value="">All Statuses</option>
                    {STATUSES.map(s => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
                </select>
            </div>

            {tickets.length === 0 ? (
                <p className="empty-state">No tickets found.</p>
            ) : (
                <div className="ticket-grid">
                    {tickets.map(ticket => (
                        <TicketCard key={ticket.id} ticket={ticket} onUpdated={handleUpdated} />
                    ))}
                </div>
            )}
        </div>
    )
}
