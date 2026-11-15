import { useEffect, useState } from 'react'

/** The current time in ms, refreshed every `every` ms (so render stays pure and "5 min ago" stays fresh). */
export function useNow(every = 10_000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), every)
    return () => clearInterval(t)
  }, [every])
  return now
}
