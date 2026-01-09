import { useNotifications } from '@/context/NotificationContext'

/**
 * Toast compatibility layer for the new notification system
 * This provides a similar API to sonner's toast to make migration easier
 */
export function useToast() {
  const { addNotification } = useNotifications()
  
  return {
    success: (message) => addNotification(message, 'success'),
    error: (message) => addNotification(message, 'error'),
    info: (message) => addNotification(message, 'info'),
    warning: (message) => addNotification(message, 'warning'),
  }
}

// Export a non-hook version for use in callbacks and event handlers
export const toast = {
  success: (message) => {
    // This is a placeholder - components should use the hook version
    console.warn('Using toast without hook - message:', message)
  },
  error: (message) => {
    console.warn('Using toast without hook - message:', message)
  },
  info: (message) => {
    console.warn('Using toast without hook - message:', message)
  },
  warning: (message) => {
    console.warn('Using toast without hook - message:', message)
  }
}
