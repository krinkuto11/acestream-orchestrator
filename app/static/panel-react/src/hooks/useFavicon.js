import { useEffect } from 'react'

const FAVICON_PATHS = {
  light: '/panel/favicon-96x96.png',
  dark: '/panel/favicon-96x96-dark.png'
}

export function useFavicon(theme) {
  useEffect(() => {
    // Get all favicon link elements
    const favicon96Link = document.querySelector('link[sizes="96x96"]')
    
    if (favicon96Link) {
      // Update the favicon based on theme
      const faviconPath = theme === 'dark' ? FAVICON_PATHS.dark : FAVICON_PATHS.light
      favicon96Link.href = faviconPath
    }
  }, [theme])
}
