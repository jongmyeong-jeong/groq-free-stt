import { useQuery } from '@tanstack/react-query'

import api from '@/api/client'
import { qk } from '@/lib/queryKeys'

export function useSystemInfo(options = {}) {
  return useQuery({
    queryKey: qk.systemInfo(),
    queryFn: api.getSystemInfo,
    ...options,
  })
}

export default useSystemInfo
