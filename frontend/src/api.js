const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8000"

export const api = {
    getTickets: (params = {}) => {
        const query = new URLSearchParams(params).toString()
        return fetch(`${BASE_URL}/api/tickets/${query ? "?" + query : ""}`).then(r => r.json())
    },

    createTicket: (data) =>
        fetch(`${BASE_URL}/api/tickets/`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
        }).then(r => r.json()),

    updateTicket: (id, data) =>
        fetch(`${BASE_URL}/api/tickets/${id}/`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(data),
        }).then(r => r.json()),

    classifyTicket: (description) =>
        fetch(`${BASE_URL}/api/tickets/classify/`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ description }),
        }).then(r => r.json()),

    getStats: () =>
        fetch(`${BASE_URL}/api/tickets/stats/`).then(r => r.json()),
}
