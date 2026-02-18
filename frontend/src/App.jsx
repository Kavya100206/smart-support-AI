import { useState, useEffect } from "react"
import { api } from "./api"
import TicketForm from "./components/TicketForm"
import TicketList from "./components/TicketList"
import StatsDashboard from "./components/StatsDashboard"
import "./App.css"

export default function App() {
    const [tickets, setTickets] = useState([])
    const [statsRefreshKey, setStatsRefreshKey] = useState(0)

    useEffect(() => {
        api.getTickets().then(setTickets)
    }, [])

    const handleTicketCreated = (newTicket) => {
        setTickets(prev => [newTicket, ...prev])
        setStatsRefreshKey(k => k + 1)
    }

    return (
        <div className="app">
            <header className="app-header">
                <h1>Smart Support AI</h1>
                <p>AI-powered ticket classification</p>
            </header>

            <main className="app-main">
                <div className="left-panel">
                    <TicketForm onTicketCreated={handleTicketCreated} />
                    <StatsDashboard refreshKey={statsRefreshKey} />
                </div>
                <div className="right-panel">
                    <TicketList tickets={tickets} setTickets={setTickets} />
                </div>
            </main>
        </div>
    )
}
