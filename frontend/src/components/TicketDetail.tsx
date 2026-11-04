import { useState } from 'react'
import { ApiRequestError } from '../api'
import {
  formatRelativeTime,
  getApprovalActionHeadline,
  getHistoryStatusText,
  getTicketPresentation,
  getTicketResultMessage,
} from '../ticketPresentation'
import type { TicketDecision, TicketDetailResponse } from '../types'
import { ApprovalCard } from './ApprovalCard'
import { ClarificationCard } from './ClarificationCard'
import { StatusBadge } from './StatusBadge'

interface TicketDetailProps {
  ticket: TicketDetailResponse | null
  isLoading: boolean
  errorMessage: string | null
  hasTickets: boolean
  onCreateTicket: () => void
  onDecide: (interruptId: string, decision: TicketDecision) => Promise<void>
  onClarify: (interruptId: string, orderReference: string) => Promise<void>
  onRetry: () => void
}

export function TicketDetail({
  ticket,
  isLoading,
  errorMessage,
  hasTickets,
  onCreateTicket,
  onDecide,
  onClarify,
  onRetry,
}: TicketDetailProps) {
  const [hasCopiedDraft, setHasCopiedDraft] = useState(false)
  const [activeDecision, setActiveDecision] = useState<{
    interruptId: string
    decision: TicketDecision
  } | null>(null)
  const [decisionErrors, setDecisionErrors] = useState<Record<string, string>>({})
  const [activeClarificationInterruptId, setActiveClarificationInterruptId] = useState<
    string | null
  >(null)

  if (isLoading) {
    return <p className="main-message">Loading ticket details…</p>
  }

  if (errorMessage) {
    return (
      <div className="main-message error-message" role="alert">
        <p>{errorMessage}</p>
        <button className="secondary-button" type="button" onClick={onRetry}>
          Try again
        </button>
      </div>
    )
  }

  if (!ticket) {
    return (
      <div className="welcome-state">
        <span className="eyebrow">Operator console</span>
        <h1>{hasTickets ? 'Select a ticket' : 'No tickets yet'}</h1>
        <p>
          {hasTickets
            ? 'Choose a ticket from the list to see its current graph state.'
            : 'Create the first ticket to run a customer request through Storekeeper.'}
        </p>
        {!hasTickets ? (
          <button className="primary-button" type="button" onClick={onCreateTicket}>
            Create a ticket
          </button>
        ) : null}
      </div>
    )
  }

  const presentation = getTicketPresentation(ticket, ticket)
  const resultMessage = getTicketResultMessage(ticket)
  const deniedGateVerdict = ticket.task_results.find(
    (result) => result.outcome === 'denied_by_policy',
  )?.gate_verdict
  const policyCitations = Array.from(
    new Set(ticket.task_results.flatMap((result) => result.policy_citations)),
  )

  const handleCopyDraft = async () => {
    if (!ticket.reply_draft) {
      return
    }
    await navigator.clipboard.writeText(ticket.reply_draft)
    setHasCopiedDraft(true)
    window.setTimeout(() => setHasCopiedDraft(false), 1800)
  }

  const handleDecision = async (interruptId: string, decision: TicketDecision) => {
    setActiveDecision({ interruptId, decision })
    setDecisionErrors((currentErrors) => ({ ...currentErrors, [interruptId]: '' }))
    try {
      await onDecide(interruptId, decision)
    } catch (error) {
      setDecisionErrors((currentErrors) => ({
        ...currentErrors,
        [interruptId]: getDecisionErrorMessage(error),
      }))
    } finally {
      setActiveDecision(null)
    }
  }

  const handleClarification = async (interruptId: string, orderReference: string) => {
    setActiveClarificationInterruptId(interruptId)
    try {
      await onClarify(interruptId, orderReference)
    } finally {
      setActiveClarificationInterruptId(null)
    }
  }

  return (
    <article className="ticket-detail">
      <header className="detail-heading">
        <div className="ticket-title-row">
          <h1>
            {ticket.status === 'waiting_for_customer'
              ? ticket.pending_clarifications.some((item) => item.choices?.length)
                ? '请选择目标订单'
                : 'Order number needed'
              : ticket.pending_approvals.length === 1
                ? getApprovalActionHeadline(ticket.pending_approvals[0])
                : ticket.pending_approvals.length > 1
                  ? `${ticket.pending_approvals.length} approvals required`
                  : 'Ticket'}
          </h1>
          {ticket.status === 'waiting_for_customer' ? (
            <StatusBadge label="Waiting for customer" tone="attention" />
          ) : ticket.status === 'pending_approval' ? (
            <StatusBadge label="Pending" tone="attention" />
          ) : (
            <span className={`detail-status detail-status-${presentation.tone}`}>
              {getHistoryStatusText(presentation)}
            </span>
          )}
        </div>
        <p className="detail-meta">
          <span>{ticket.ticket_id}</span>
          <span aria-hidden="true">·</span>
          <span>received {formatRelativeTime(ticket.created_at)}</span>
        </p>
      </header>

      <section className="content-card customer-message-card">
        <h2 className="card-label">Customer message</h2>
        <p>{ticket.ticket_text}</p>
      </section>

      {ticket.order_resolution && ticket.order_resolution.status !== 'skipped' ? (
        <section className="content-card order-discovery-card">
          <h2 className="card-label">订单查找记录</h2>
          <p>状态：{ticket.order_resolution.status} · 目标订单：{ticket.order_resolution.selected_reference ?? '尚未确定'}</p>
          <ol>
            {ticket.order_resolution.events.filter((event) => event.kind === 'tool').map((event, index) => (
              <li key={index}>
                <details>
                  <summary><code>{event.name}</code></summary>
                  <pre>{JSON.stringify({ input: event.arguments, output: event.result }, null, 2)}</pre>
                </details>
              </li>
            ))}
          </ol>
          <p>这里只查找和绑定订单，取消操作仍须通过规则检查与人工审批。</p>
        </section>
      ) : null}

      {ticket.clarification_history.length > 0 ? (
        <section className="content-card clarification-history-card">
          <h2 className="card-label">Customer follow-up</h2>
          <dl className="clarification-history-list">
            {ticket.clarification_history.map((clarificationHistoryEntry, entryIndex) => (
              <div key={`${clarificationHistoryEntry.task_id}-${entryIndex}`}>
                <dt>Order number · {clarificationHistoryEntry.task_id}</dt>
                <dd>{clarificationHistoryEntry.value}</dd>
              </div>
            ))}
          </dl>
        </section>
      ) : null}

      {ticket.status !== 'pending_approval' && resultMessage ? (
        <div className={`result-banner result-banner-${presentation.tone}`}>
          {resultMessage}
        </div>
      ) : null}

      {deniedGateVerdict ? (
        <div className="gate-status-strip gate-status-denied">
          <div>
            <strong>✕ Gate denied</strong>
            <span aria-hidden="true"> — </span>
            <code>{deniedGateVerdict.rule}</code>
          </div>
          <p>{deniedGateVerdict.reason}</p>
        </div>
      ) : null}

      {ticket.status === 'waiting_for_customer'
        ? ticket.pending_clarifications.map((clarification) => (
            <ClarificationCard
              key={clarification.interrupt_id}
              clarification={clarification}
              isDisabled={
                activeClarificationInterruptId !== null || activeDecision !== null
              }
              onClarify={handleClarification}
              onRefresh={onRetry}
            />
          ))
        : null}

      {ticket.status === 'pending_approval'
        ? ticket.pending_approvals.map((approval) => (
            <ApprovalCard
              key={approval.interrupt_id}
              approval={approval}
              activeDecision={
                activeDecision?.interruptId === approval.interrupt_id
                  ? activeDecision.decision
                  : null
              }
              isDecisionDisabled={
                activeDecision !== null || activeClarificationInterruptId !== null
              }
              errorMessage={decisionErrors[approval.interrupt_id] || null}
              onDecide={(decision) =>
                void handleDecision(approval.interrupt_id, decision)
              }
              onRefresh={onRetry}
            />
          ))
        : null}

      {ticket.reply_draft ? (
        <section className="content-card reply-card">
          <div className="reply-card-header">
            <h2 className="card-label">Drafted reply — not sent</h2>
            <button
              className="secondary-button copy-button"
              type="button"
              onClick={handleCopyDraft}
            >
              {hasCopiedDraft ? 'Copied' : 'Copy draft'}
            </button>
          </div>
          <div className="reply-copy">{ticket.reply_draft}</div>
          {policyCitations.length > 0 ? (
            <div className="citation-list">
              {policyCitations.map((citation) => (
                <span className="citation-chip" key={citation}>
                  <span aria-hidden="true">📄</span> {citation}
                </span>
              ))}
            </div>
          ) : null}
          <p className="draft-notice">
            Replies are always drafts. Copy this into your helpdesk or email; Storekeeper
            never messages customers directly.
          </p>
        </section>
      ) : null}
    </article>
  )
}

function getDecisionErrorMessage(error: unknown): string {
  if (error instanceof ApiRequestError) {
    if (error.statusCode === 409) {
      return 'This ticket was already decided. Refresh it to see the latest result.'
    }
    if (error.statusCode === 404) {
      return 'This ticket no longer exists in the ticket registry.'
    }
  }
  if (error instanceof Error) {
    return error.message
  }
  return 'The decision could not be completed. Please try again.'
}
