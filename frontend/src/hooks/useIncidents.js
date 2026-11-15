import { useQuery } from '@tanstack/react-query'
import { getIncidentTickets } from '../services/incidentsApi'

/**
 * Incident tickets from the social-media agents. They run on the user's own machine and may simply
 * not be started, so there is no retry: the page shows how to start them instead.
 */
export function useIncidentTickets() {
  return useQuery({
    queryKey: ['incident-tickets'],
    queryFn: () => getIncidentTickets(200),
    staleTime: 30 * 1000,
    refetchInterval: 60 * 1000,
    retry: false,
  })
}
