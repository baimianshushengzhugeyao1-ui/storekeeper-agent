import type { ApprovalPayload, TicketDetailResponse, TicketSummary } from './types'

export type StatusTone = 'neutral' | 'success' | 'danger' | 'attention'

export interface TicketPresentation {
  label: string
  tone: StatusTone
}

export function getTicketPresentation(
  ticket: TicketSummary,
  ticketDetail?: TicketDetailResponse | null,
): TicketPresentation {
  if (ticket.status === 'waiting_for_customer') {
    return { label: 'Waiting for customer', tone: 'attention' }
  }
  if (ticket.status === 'pending_approval') {
    return { label: 'Awaiting approval', tone: 'attention' }
  }

  if (ticketDetail?.ticket_outcome === 'escalated_to_human') {
    return { label: 'Escalated to you', tone: 'neutral' }
  }

  const taskOutcomes = ticketDetail?.task_results.map((result) => result.outcome) ?? []
  if (taskOutcomes.includes('rejected_by_human')) {
    return { label: 'Rejected', tone: 'neutral' }
  }
  if (taskOutcomes.includes('denied_by_policy')) {
    return { label: 'Denied by policy', tone: 'danger' }
  }
  if (taskOutcomes.includes('failed')) {
    return { label: 'Needs attention', tone: 'danger' }
  }
  if (taskOutcomes.includes('executed')) {
    if (taskOutcomes.includes('submitted')) {
      return { label: 'Submission pending confirmation', tone: 'attention' }
    }
    return { label: 'Executed', tone: 'success' }
  }
  if (taskOutcomes.includes('submitted')) {
    return { label: 'Submission pending confirmation', tone: 'attention' }
  }
  if (taskOutcomes.includes('answered')) {
    return { label: 'Answered', tone: 'success' }
  }
  return { label: 'Resolved', tone: 'neutral' }
}

export function getTicketResultMessage(ticket: TicketDetailResponse): string | null {
  if (ticket.status === 'waiting_for_customer') {
    return 'Waiting for the customer’s order number before this request can continue.'
  }
  if (ticket.status === 'pending_approval') {
    return 'This action is waiting for an operator decision.'
  }
  if (ticket.ticket_outcome === 'escalated_to_human') {
    return ticket.escalation_reason ?? 'This ticket needs manual follow-up.'
  }

  const taskOutcomes = ticket.task_results.map((result) => result.outcome)
  if (taskOutcomes.includes('rejected_by_human')) {
    return 'You rejected this action. No changes were made to the order.'
  }
  if (taskOutcomes.includes('denied_by_policy')) {
    return 'The request was denied by store policy. No Shopify write was attempted.'
  }
  if (taskOutcomes.includes('failed')) {
    return 'The requested action could not be completed and needs manual follow-up.'
  }
  if (taskOutcomes.includes('executed')) {
    if (taskOutcomes.includes('submitted')) {
      return 'Some requests were submitted. Cancellation completion and refund settlement are not verified.'
    }
    return 'The approved action was completed successfully.'
  }
  if (taskOutcomes.includes('submitted')) {
    return 'The cancellation request was submitted. Completion and refund settlement are not verified.'
  }
  if (taskOutcomes.includes('answered')) {
    return 'The policy question was answered from the verified sources below.'
  }
  return null
}

export function getApprovalActionHeadline(approval: ApprovalPayload): string {
  if (approval.action === 'cancel_order') {
    return `Cancel order ${approval.order}`
  }
  if (approval.action === 'issue_refund') {
    return `Refund order ${approval.order}`
  }
  return `Change address on ${approval.order}`
}

export function getHistoryStatusText(presentation: TicketPresentation): string {
  if (presentation.label === 'Executed' || presentation.label === 'Answered') {
    return `✓ ${presentation.label}`
  }
  if (presentation.label === 'Denied by policy') {
    return `✕ ${presentation.label}`
  }
  return presentation.label
}

export function formatRelativeTime(dateValue: string): string {
  const timestamp = new Date(dateValue).getTime()
  if (Number.isNaN(timestamp)) {
    return dateValue
  }

  const elapsedSeconds = Math.round((timestamp - Date.now()) / 1000)
  const formatter = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })
  const relativeUnits: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ['year', 31_536_000],
    ['month', 2_592_000],
    ['week', 604_800],
    ['day', 86_400],
    ['hour', 3_600],
    ['minute', 60],
  ]

  for (const [unit, secondsInUnit] of relativeUnits) {
    if (Math.abs(elapsedSeconds) >= secondsInUnit) {
      return formatter.format(Math.round(elapsedSeconds / secondsInUnit), unit)
    }
  }
  return 'just now'
}
