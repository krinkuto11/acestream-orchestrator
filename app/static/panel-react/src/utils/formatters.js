export function formatTime(dateStr) {
  if (!dateStr) return 'Never'
  return new Date(dateStr).toLocaleString()
}

export function timeAgo(dateStr) {
  if (!dateStr) return 'Never'
  const now = new Date()
  const date = new Date(dateStr)
  const diff = now - date
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return 'Just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

export function formatBytes(bytes) {
  if (bytes == null) return 'N/A'
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i]
}

export function formatBytesPerSecond(bytesPerSecond) {
  if (bytesPerSecond == null) return 'N/A'
  if (bytesPerSecond === 0) return '0 B/s'
  const k = 1024
  const sizes = ['B/s', 'KB/s', 'MB/s', 'GB/s', 'TB/s']
  const i = Math.floor(Math.log(bytesPerSecond) / Math.log(k))
  return Math.round((bytesPerSecond / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i]
}

/**
 * Convert a country code to a flag emoji
 * @param {string} countryCode - ISO 3166-1 alpha-2 country code (e.g., "US", "GB")
 * @returns {string} Flag emoji or empty string if invalid
 */
export function countryCodeToFlag(countryCode) {
  if (!countryCode || countryCode === '??' || countryCode.length !== 2) {
    return ''
  }
  
  // Convert country code to regional indicator symbols
  // Each letter is offset by 127397 from its ASCII code to get the regional indicator
  return String.fromCodePoint(
    ...countryCode.toUpperCase().split('').map(c => 127397 + c.charCodeAt())
  )
}
