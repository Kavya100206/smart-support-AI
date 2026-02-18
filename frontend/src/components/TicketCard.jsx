import { api } from "../api"

const PRIORITY_COLORS = { low: "#22c55e", medium: "#f59e0b", high: "#ef4444", critical: "#7c3aed" }
const STATUS_ORDER = ["open", "in_progress", "resolved", "closed"]

export default function TicketCard({ ticket, onUpdated }) {
    const nextStatus = STATUS_ORDER[STATUS_ORDER.indexOf(ticket.status) + 1]

    const advance = async () => {
        if (!nextStatus) return
        const updated = await api.updateTicket(ticket.id, { status: nextStatus })
        onUpdated(updated)
    }

    return (
        <div className="ticket-card">
            <div className="ticket-card-header">
                <span className="ticket-title">{ticket.title}</span>
                <span className="priority-badge" style={{ background: PRIORITY_COLORS[ticket.priority] }}>
                    {ticket.priority}
                </span>
            </div>
            <p className="ticket-description">{ticket.description.slice(0, 120)}{ticket.description.length > 120 ? "…" : ""}</p>
            <div className="ticket-meta">
                <span className="tag">{ticket.category}</span>
                <span className="tag status-tag">{ticket.status.replace("_", " ")}</span>
                <span className="ticket-date">{new Date(ticket.created_at).toLocaleDateString()}</span>
            </div>
            {nextStatus && (
                <button className="advance-btn" onClick={advance}>
                    → Mark as {nextStatus.replace("_", " ")}
                </button>
            )}
        </div>
    )
}
