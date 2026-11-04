import { getApprovalActionHeadline } from '../ticketPresentation'
import type {
  ApprovalPayload,
  ShippingAddress,
  TicketDecision,
} from '../types'

interface ApprovalCardProps {
  approval: ApprovalPayload
  activeDecision: TicketDecision | null
  isDecisionDisabled: boolean
  errorMessage: string | null
  onDecide: (decision: TicketDecision) => void
  onRefresh: () => void
}

const ACTION_LABELS: Record<ApprovalPayload['action'], string> = {
  cancel_order: 'Cancel order',
  issue_refund: 'Issue full refund',
  update_shipping_address: 'Update shipping address',
}

export function ApprovalCard({
  approval,
  activeDecision,
  isDecisionDisabled,
  errorMessage,
  onDecide,
  onRefresh,
}: ApprovalCardProps) {
  const approveButtonLabel = getApproveButtonLabel(approval)
  const approvalHeadingId = `approval-heading-${approval.interrupt_id}`

  return (
    <section className="content-card approval-card" aria-labelledby={approvalHeadingId}>
      <span className="eyebrow approval-eyebrow">Awaiting your approval</span>
      <h2 id={approvalHeadingId}>{getApprovalActionHeadline(approval)}</h2>

      <dl className="approval-facts-grid">
        <div>
          <dt>Customer wrote</dt>
          <dd className="monospace-value">
            {approval.requested_reference ?? 'No reference provided'}
          </dd>
        </div>
        <div>
          <dt>Matched order</dt>
          <dd className="matched-order-value">
            <span className="monospace-value">{approval.order}</span>
            {approval.requested_reference === approval.order ? (
              <span className="exact-match">✓ exact match</span>
            ) : null}
          </dd>
        </div>
        <div>
          <dt>Requested action</dt>
          <dd>{ACTION_LABELS[approval.action]}</dd>
        </div>
        <div>
          <dt>Amount</dt>
          <dd className="amount-value">{approval.amount}</dd>
        </div>
      </dl>

      {approval.current_shipping_address || approval.new_shipping_address ? (
        <div className="address-comparison">
          <AddressPanel
            heading="Current shipping address"
            address={approval.current_shipping_address}
          />
          <AddressPanel
            heading="Proposed shipping address"
            address={approval.new_shipping_address}
            emphasized
          />
        </div>
      ) : null}

      {approval.flags.length > 0 ? (
        <div className="flag-list approval-flag-list">
          {approval.flags.map((flag) => (
            <span className="safety-flag" key={flag}>{flag}</span>
          ))}
        </div>
      ) : null}

      <div className="gate-status-strip gate-status-passed">
        <div>
          <strong>✓ Gate passed</strong>
          <span aria-hidden="true"> — </span>
          <code>{approval.gate_rule}</code>
        </div>
        <p>{approval.gate_reason}</p>
      </div>

      {errorMessage ? (
        <div className="decision-error" role="alert">
          <p>{errorMessage}</p>
          <button className="text-button" type="button" onClick={onRefresh}>
            Refresh ticket
          </button>
        </div>
      ) : null}

      <div className="decision-footer">
        <div className="decision-actions">
          <button
            className="primary-button approve-button"
            type="button"
            disabled={isDecisionDisabled}
            onClick={() => onDecide('approve')}
          >
            {activeDecision === 'approve' ? 'Executing…' : approveButtonLabel}
          </button>
          <button
            className="secondary-button reject-button"
            type="button"
            disabled={isDecisionDisabled}
            onClick={() => onDecide('reject')}
          >
            {activeDecision === 'reject' ? 'Rejecting…' : 'Reject'}
          </button>
        </div>
        <p>
          {typeof document !== 'undefined' && (document.body.dataset.offlineDemo === 'true' || document.body.dataset.orderAgentPreview)
            ? 'Fixture orders: approval records a simulated action only. No real store or payment changes.'
            : 'Approving executes a real write on your Shopify store. Rejecting makes no changes.'}
        </p>
      </div>
    </section>
  )
}

interface AddressPanelProps {
  heading: string
  address: ShippingAddress | null
  emphasized?: boolean
}

function AddressPanel({ heading, address, emphasized = false }: AddressPanelProps) {
  const addressLines = address ? getAddressLines(address) : []
  return (
    <div className={`address-panel${emphasized ? ' proposed' : ''}`}>
      <span>{heading}</span>
      {addressLines.length > 0 ? (
        <address>
          {addressLines.map((addressLine) => (
            <span key={addressLine}>{addressLine}</span>
          ))}
        </address>
      ) : (
        <p>Not available</p>
      )}
    </div>
  )
}

function getAddressLines(address: ShippingAddress): string[] {
  const recipientName = [address.first_name, address.last_name].filter(Boolean).join(' ')
  const cityLine = [address.city, address.province, address.zip].filter(Boolean).join(', ')
  return [
    recipientName,
    address.company,
    address.address1,
    address.address2,
    cityLine,
    address.country,
    address.phone,
  ].filter((addressLine): addressLine is string => Boolean(addressLine))
}

function getApproveButtonLabel(approval: ApprovalPayload): string {
  if (approval.action === 'cancel_order') {
    return `Approve — cancel order ${approval.order}`
  }
  if (approval.action === 'issue_refund') {
    return `Approve — refund order ${approval.order}`
  }
  return `Approve — update address for ${approval.order}`
}
