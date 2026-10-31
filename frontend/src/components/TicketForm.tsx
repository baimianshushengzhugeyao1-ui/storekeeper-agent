import { useState, type FormEvent } from 'react'

interface TicketFormProps {
  onCancel: () => void
  onCreateTicket: (ticketText: string) => Promise<void>
}

export function TicketForm({ onCancel, onCreateTicket }: TicketFormProps) {
  const [ticketText, setTicketText] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const cleanedTicketText = ticketText.trim()
    if (!cleanedTicketText) {
      setErrorMessage('Enter the customer message before creating the ticket.')
      return
    }

    setIsSubmitting(true)
    setErrorMessage(null)
    try {
      await onCreateTicket(cleanedTicketText)
    } catch (error) {
      setErrorMessage(
        error instanceof Error ? error.message : 'The ticket could not be created.',
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <section className="new-ticket-view" aria-labelledby="new-ticket-heading">
      <div className="detail-heading">
        <div>
          <span className="eyebrow">New request</span>
          <h1 id="new-ticket-heading">Create a ticket</h1>
          <p>The customer message enters the guarded Storekeeper workflow.</p>
        </div>
      </div>

      <form className="ticket-form content-card" onSubmit={handleSubmit}>
        <label htmlFor="ticket-text">Customer message</label>
        <textarea
          id="ticket-text"
          value={ticketText}
          onChange={(event) => setTicketText(event.target.value)}
          placeholder="How long is your warranty?"
          rows={7}
          autoFocus
          disabled={isSubmitting}
        />
        <p className="form-help">
          Storekeeper classifies the request, checks policy, and returns a draft reply.
        </p>
        {errorMessage ? (
          <p className="form-error" role="alert">
            {errorMessage}
          </p>
        ) : null}
        <div className="form-actions">
          <button
            className="secondary-button"
            type="button"
            onClick={onCancel}
            disabled={isSubmitting}
          >
            Cancel
          </button>
          <button className="primary-button" type="submit" disabled={isSubmitting}>
            {isSubmitting ? 'Running workflow…' : 'Create ticket'}
          </button>
        </div>
      </form>
    </section>
  )
}
