import { useState, type FormEvent } from 'react'
import { ApiRequestError } from '../api'
import type { ClarificationPayload } from '../types'

interface ClarificationCardProps {
  clarification: ClarificationPayload
  isDisabled: boolean
  onClarify: (interruptId: string, orderReference: string) => Promise<void>
  onRefresh: () => void
}

export function ClarificationCard({
  clarification,
  isDisabled,
  onClarify,
  onRefresh,
}: ClarificationCardProps) {
  const [orderReference, setOrderReference] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [shouldRefreshTicket, setShouldRefreshTicket] = useState(false)
  const headingId = `clarification-heading-${clarification.interrupt_id}`
  const orderReferenceId = `order-reference-${clarification.interrupt_id}`
  const helpId = `${orderReferenceId}-help`
  const errorId = `${orderReferenceId}-error`
  const choices = clarification.choices ?? []

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (isDisabled || isSubmitting) {
      return
    }
    const cleanedOrderReference = orderReference.trim()
    setShouldRefreshTicket(false)
    if (!/^#?[0-9]{1,10}$/.test(cleanedOrderReference)) {
      setErrorMessage('Enter 1–10 digits, with an optional #, for example #1036.')
      return
    }

    setIsSubmitting(true)
    setErrorMessage(null)
    try {
      await onClarify(clarification.interrupt_id, cleanedOrderReference)
    } catch (error) {
      setErrorMessage(getClarificationErrorMessage(error))
      setShouldRefreshTicket(error instanceof ApiRequestError && error.statusCode === 409)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <section className="content-card clarification-card" aria-labelledby={headingId}>
      <span className="eyebrow approval-eyebrow">Waiting for customer</span>
      <h2 id={headingId}>{clarification.question}</h2>
      <p className="clarification-task">Request: {clarification.task_id}</p>
      <form className="clarification-form" onSubmit={handleSubmit} aria-busy={isSubmitting}>
        <label htmlFor={orderReferenceId}>{choices.length ? '请选择订单' : 'Order number'}</label>
        {choices.length ? (
          <select
            id={orderReferenceId}
            value={orderReference}
            onChange={(event) => setOrderReference(event.target.value)}
            disabled={isDisabled || isSubmitting}
            required
          >
            <option value="">请选择要取消的订单</option>
            {choices.map((order) => (
              <option key={order.order_reference} value={order.order_reference}>
                {order.order_reference} · {order.summary} · {order.amount} {order.currency} · {order.placed_at}
              </option>
            ))}
          </select>
        ) : <input
          id={orderReferenceId}
          type="text"
          value={orderReference}
          onChange={(event) => setOrderReference(event.target.value)}
          placeholder="#1036"
          autoComplete="off"
          spellCheck={false}
          disabled={isDisabled || isSubmitting}
          aria-describedby={`${helpId}${errorMessage ? ` ${errorId}` : ''}`}
          aria-required="true"
        />}
        <p className="form-help" id={helpId}>
          {choices.length
            ? '查询返回多个候选订单，请确认目标；选择订单不代表批准取消。'
            : 'Enter the order number supplied by the customer: 1–10 digits, with an optional #.'}
        </p>
        {errorMessage ? (
          <div className="decision-error" id={errorId} role="alert">
            <p>{errorMessage}</p>
            {shouldRefreshTicket ? (
              <button className="text-button" type="button" onClick={onRefresh}>
                Refresh ticket
              </button>
            ) : null}
          </div>
        ) : null}
        <div className="form-actions">
          <button
            className="primary-button"
            type="submit"
            disabled={isDisabled || isSubmitting}
          >
            {isSubmitting ? 'Continuing…' : choices.length ? '确认目标订单' : 'Submit order number'}
          </button>
        </div>
      </form>
    </section>
  )
}

function getClarificationErrorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    if (error.statusCode === 409) {
      return 'This request has changed. Refresh the ticket before submitting again.'
    }
    if (error.statusCode === 404) {
      return 'This ticket no longer exists in the ticket registry.'
    }
    if (error.statusCode === 422) {
      return 'Enter 1–10 digits, with an optional #, for example #1036.'
    }
  }
  return error instanceof Error
    ? error.message
    : 'The order number could not be submitted. Please try again.'
}
