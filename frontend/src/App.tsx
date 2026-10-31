import { useCallback, useEffect, useState } from 'react'
import './App.css'
import { clarifyTicket, createTicket, decideTicket, getTicket, listTickets } from './api'
import { TicketDetail } from './components/TicketDetail'
import { TicketForm } from './components/TicketForm'
import { TicketList } from './components/TicketList'
import type { TicketDecision, TicketDetailResponse, TicketSummary } from './types'

function App() {
  const [tickets, setTickets] = useState<TicketSummary[]>([])
  const [selectedTicketId, setSelectedTicketId] = useState<string | null>(null)
  const [selectedTicket, setSelectedTicket] = useState<TicketDetailResponse | null>(null)
  const [ticketDetailsById, setTicketDetailsById] = useState<
    Record<string, TicketDetailResponse>
  >({})
  const [isCreatingTicket, setIsCreatingTicket] = useState(false)
  const [isLoadingTickets, setIsLoadingTickets] = useState(true)
  const [isLoadingDetail, setIsLoadingDetail] = useState(false)
  const [detailRefreshKey, setDetailRefreshKey] = useState(0)
  const [listError, setListError] = useState<string | null>(null)
  const [detailError, setDetailError] = useState<string | null>(null)

  const refreshTickets = useCallback(async (preferredTicketId?: string) => {
    setListError(null)
    try {
      const latestTickets = await listTickets()
      setTickets(latestTickets)
      const detailResults = await Promise.allSettled(
        latestTickets.map((ticket) => getTicket(ticket.ticket_id)),
      )
      setTicketDetailsById((currentDetails) => {
        const nextDetails = { ...currentDetails }
        detailResults.forEach((detailResult) => {
          if (detailResult.status === 'fulfilled') {
            nextDetails[detailResult.value.ticket_id] = detailResult.value
          }
        })
        return nextDetails
      })
      setSelectedTicketId((currentTicketId) => {
        if (preferredTicketId) {
          return preferredTicketId
        }
        if (
          currentTicketId &&
          latestTickets.some((ticket) => ticket.ticket_id === currentTicketId)
        ) {
          return currentTicketId
        }
        return latestTickets[0]?.ticket_id ?? null
      })
    } catch (error) {
      setListError(getErrorMessage(error))
    } finally {
      setIsLoadingTickets(false)
    }
  }, [])

  useEffect(() => {
    void refreshTickets()
  }, [refreshTickets])

  useEffect(() => {
    if (!selectedTicketId || isCreatingTicket) {
      return
    }

    let shouldUseResponse = true
    setIsLoadingDetail(true)
    setDetailError(null)

    void getTicket(selectedTicketId)
      .then((ticketDetail) => {
        if (shouldUseResponse) {
          setSelectedTicket(ticketDetail)
          setTicketDetailsById((currentDetails) => ({
            ...currentDetails,
            [ticketDetail.ticket_id]: ticketDetail,
          }))
        }
      })
      .catch((error) => {
        if (shouldUseResponse) {
          setDetailError(getErrorMessage(error))
          setSelectedTicket(null)
        }
      })
      .finally(() => {
        if (shouldUseResponse) {
          setIsLoadingDetail(false)
        }
      })

    return () => {
      shouldUseResponse = false
    }
  }, [detailRefreshKey, isCreatingTicket, selectedTicketId])

  const handleSelectTicket = (ticketId: string) => {
    setIsCreatingTicket(false)
    setSelectedTicketId(ticketId)
  }

  const handleCreateTicket = async (ticketText: string) => {
    const createdTicket = await createTicket(ticketText)
    setSelectedTicket(createdTicket)
    setTicketDetailsById((currentDetails) => ({
      ...currentDetails,
      [createdTicket.ticket_id]: createdTicket,
    }))
    setSelectedTicketId(createdTicket.ticket_id)
    setIsCreatingTicket(false)
    await refreshTickets(createdTicket.ticket_id)
  }

  const handleTicketDecision = async (
    interruptId: string,
    decision: TicketDecision,
  ) => {
    if (!selectedTicketId) {
      throw new Error('Select a ticket before making a decision.')
    }

    const decidedTicket = await decideTicket(selectedTicketId, interruptId, decision)
    setSelectedTicket(decidedTicket)
    setTicketDetailsById((currentDetails) => ({
      ...currentDetails,
      [decidedTicket.ticket_id]: decidedTicket,
    }))
    await refreshTickets(decidedTicket.ticket_id)
  }

  const handleTicketClarification = async (
    interruptId: string,
    orderReference: string,
  ) => {
    if (!selectedTicketId) {
      throw new Error('Select a ticket before submitting an order number.')
    }

    const clarifiedTicket = await clarifyTicket(
      selectedTicketId,
      interruptId,
      orderReference,
    )
    setSelectedTicket((currentTicket) =>
      currentTicket?.ticket_id === clarifiedTicket.ticket_id
        ? clarifiedTicket
        : currentTicket,
    )
    setTicketDetailsById((currentDetails) => ({
      ...currentDetails,
      [clarifiedTicket.ticket_id]: clarifiedTicket,
    }))
    await refreshTickets()
  }

  const pendingApprovalCount = tickets.reduce((approvalCount, ticket) => {
    if (ticket.status !== 'pending_approval') {
      return approvalCount
    }
    return approvalCount + (ticketDetailsById[ticket.ticket_id]?.pending_approvals.length ?? 1)
  }, 0)

  return (
    <div className="operator-console">
      <header className="app-header">
        <div className="brand-group">
          <span className="wordmark">storekeeper</span>
          <span className="brand-divider" aria-hidden="true" />
          <span className="console-label">Operator console</span>
        </div>
        <div className="header-status">
          <span className="store-label">
            {typeof document !== 'undefined' && document.body.dataset.offlineDemo === 'true'
              ? 'Offline demo'
              : 'Development store'}
          </span>
          <span
            className={`approval-summary${
              pendingApprovalCount === 0 ? ' approval-summary-clear' : ''
            }`}
          >
            {pendingApprovalCount === 0
              ? 'No approvals pending'
              : `${pendingApprovalCount} awaiting approval`}
          </span>
        </div>
      </header>

      <div className="console-layout">
        <TicketList
          tickets={tickets}
          ticketDetailsById={ticketDetailsById}
          selectedTicketId={selectedTicketId}
          isCreatingTicket={isCreatingTicket}
          isLoading={isLoadingTickets}
          errorMessage={listError}
          onCreateTicket={() => setIsCreatingTicket(true)}
          onSelectTicket={handleSelectTicket}
          onRetry={() => void refreshTickets()}
        />

        <main className="main-panel">
          <div className="main-panel-content">
            {isCreatingTicket ? (
              <TicketForm
                onCancel={() => setIsCreatingTicket(false)}
                onCreateTicket={handleCreateTicket}
              />
            ) : (
              <TicketDetail
                key={selectedTicketId ?? 'no-ticket'}
                ticket={selectedTicket}
                isLoading={isLoadingDetail}
                errorMessage={detailError}
                hasTickets={tickets.length > 0}
                onCreateTicket={() => setIsCreatingTicket(true)}
                onDecide={handleTicketDecision}
                onClarify={handleTicketClarification}
                onRetry={() => setDetailRefreshKey((currentKey) => currentKey + 1)}
              />
            )}
          </div>
        </main>
      </div>
    </div>
  )
}

function getErrorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message
  }
  return 'Something went wrong. Please try again.'
}

export default App
