import type { TicketDecision, TicketDetailResponse, TicketSummary } from './types'

const TICKETS_API_PATH = '/api/tickets'

export async function listTickets(): Promise<TicketSummary[]> {
  return requestJson<TicketSummary[]>(TICKETS_API_PATH)
}

export async function getTicket(ticketId: string): Promise<TicketDetailResponse> {
  return requestJson<TicketDetailResponse>(
    `${TICKETS_API_PATH}/${encodeURIComponent(ticketId)}`,
  )
}

export async function createTicket(ticketText: string): Promise<TicketDetailResponse> {
  return requestJson<TicketDetailResponse>(TICKETS_API_PATH, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ ticket_text: ticketText }),
  })
}

export async function decideTicket(
  ticketId: string,
  interruptId: string,
  decision: TicketDecision,
): Promise<TicketDetailResponse> {
  return requestJson<TicketDetailResponse>(
    `${TICKETS_API_PATH}/${encodeURIComponent(ticketId)}/decision`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ interrupt_id: interruptId, decision }),
    },
  )
}

export async function clarifyTicket(
  ticketId: string,
  interruptId: string,
  orderReference: string,
): Promise<TicketDetailResponse> {
  return requestJson<TicketDetailResponse>(
    `${TICKETS_API_PATH}/${encodeURIComponent(ticketId)}/clarification`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        interrupt_id: interruptId,
        order_reference: orderReference,
      }),
    },
  )
}

export class ApiRequestError extends Error {
  readonly statusCode: number

  constructor(statusCode: number, message: string) {
    super(message)
    this.name = 'ApiRequestError'
    this.statusCode = statusCode
  }
}

async function requestJson<ResponseBody>(
  path: string,
  requestOptions?: RequestInit,
): Promise<ResponseBody> {
  const response = await fetch(path, requestOptions)
  if (!response.ok) {
    const errorBody = (await response.json().catch(() => null)) as {
      detail?: string
    } | null
    throw new ApiRequestError(
      response.status,
      errorBody?.detail ?? `Request failed with status ${response.status}.`,
    )
  }
  return response.json() as Promise<ResponseBody>
}
