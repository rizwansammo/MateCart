import { createApiClient } from '@matecart/shared-ui';
export const authApi = createApiClient({ csrfCookie: 'mc_console_csrf' });
