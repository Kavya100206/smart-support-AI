import { useState, useRef } from "react"
import { api } from "../api"

const CATEGORIES = ["billing", "technical", "account", "general"]
const PRIORITIES = ["low", "medium", "high", "critical"]

export default function TicketForm({ onTicketCreated }) {
    const [form, setForm] = useState({ title: "", description: "", category: "", priority: "" })
    const [classifying, setClassifying] = useState(false)
    const [submitting, setSubmitting] = useState(false)
    const [error, setError] = useState("")
    const classifyTimeout = useRef(null)

    const handleDescriptionBlur = async () => {
        if (!form.description.trim()) return
        setClassifying(true)
        try {
            const result = await api.classifyTicket(form.description)
            setForm(prev => ({
                ...prev,
                category: result.suggested_category || prev.category,
                priority: result.suggested_priority || prev.priority,
            }))
        } catch {
        } finally {
            setClassifying(false)
        }
    }

    const handleSubmit = async (e) => {
        e.preventDefault()
        if (!form.title || !form.description || !form.category || !form.priority) {
            setError("All fields are required.")
            return
        }
        setSubmitting(true)
        setError("")
        try {
            const ticket = await api.createTicket(form)
            if (ticket.id) {
                setForm({ title: "", description: "", category: "", priority: "" })
                onTicketCreated(ticket)
            } else {
                setError("Failed to create ticket. Check all fields.")
            }
        } catch {
            setError("Network error. Please try again.")
        } finally {
            setSubmitting(false)
        }
    }

    return (
        <form className="ticket-form" onSubmit={handleSubmit}>
            <h2>Submit a Ticket</h2>

            <div className="form-group">
                <label>Title</label>
                <input
                    type="text"
                    maxLength={200}
                    placeholder="Brief summary of the issue"
                    value={form.title}
                    onChange={e => setForm(prev => ({ ...prev, title: e.target.value }))}
                />
            </div>

            <div className="form-group">
                <label>Description</label>
                <textarea
                    rows={4}
                    placeholder="Describe your issue in detail..."
                    value={form.description}
                    onChange={e => setForm(prev => ({ ...prev, description: e.target.value }))}
                    onBlur={handleDescriptionBlur}
                />
                {classifying && <span className="classifying-hint">🤖 AI is suggesting category & priority...</span>}
            </div>

            <div className="form-row">
                <div className="form-group">
                    <label>Category {classifying && "⏳"}</label>
                    <select value={form.category} onChange={e => setForm(prev => ({ ...prev, category: e.target.value }))}>
                        <option value="">Select category</option>
                        {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                </div>

                <div className="form-group">
                    <label>Priority {classifying && "⏳"}</label>
                    <select value={form.priority} onChange={e => setForm(prev => ({ ...prev, priority: e.target.value }))}>
                        <option value="">Select priority</option>
                        {PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}
                    </select>
                </div>
            </div>

            {error && <p className="form-error">{error}</p>}

            <button type="submit" disabled={submitting || classifying}>
                {submitting ? "Submitting..." : "Submit Ticket"}
            </button>
        </form>
    )
}
