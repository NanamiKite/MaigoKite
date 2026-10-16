import type { DeskImage } from './desk-image'

const name = (import.meta.env.VITE_MOCK === '1' ? 'demo.' : '') + 'maigokite.desk'
export const deskPreferenceKey = name + '.preferences'

async function database(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') return reject(new Error('图片存储不可用。'))
    const request = indexedDB.open(name, 1)
    let failed = false
    request.onupgradeneeded = () => request.result.createObjectStore('images', { keyPath: 'id' })
    request.onsuccess = () => {
      if (failed) {
        request.result.close()
        return
      }
      request.result.onversionchange = () => request.result.close()
      resolve(request.result)
    }
    request.onerror = request.onblocked = () => {
      failed = true
      reject(new Error('图片存储不可用。'))
    }
  })
}

async function transaction<T>(
  mode: IDBTransactionMode,
  action: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await database()
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction('images', mode),
        request = action(tx.objectStore('images'))
      tx.oncomplete = () => resolve(request.result)
      tx.onerror = tx.onabort = () => reject(new Error('图片存储不可用。'))
    })
  } finally {
    db.close()
  }
}

export const putDeskImage = (image: DeskImage) =>
  transaction('readwrite', (store) => store.put(image))
export const deleteDeskImage = (id: string) => transaction('readwrite', (store) => store.delete(id))
export async function getDeskImage(id: string): Promise<DeskImage | null> {
  const value: unknown = await transaction('readonly', (store) => store.get(id))
  if (!value || typeof value !== 'object') return null
  const image = value as Partial<DeskImage>
  return image.id === id &&
    image.clear instanceof Blob &&
    image.soft instanceof Blob &&
    typeof image.width === 'number' &&
    typeof image.height === 'number'
    ? (image as DeskImage)
    : null
}
