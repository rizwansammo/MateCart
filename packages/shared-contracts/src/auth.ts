/** Runtime-validated authentication responses, mirroring the committed OpenAPI schemas. */
export type JsonObject = Record<string, unknown>;
export type Decoder<T> = (value: unknown) => T;
export function object(value: unknown): JsonObject {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error('Invalid response');
  return value as JsonObject;
}
export function string(value: unknown): string {
  if (typeof value !== 'string') throw new Error('Invalid response');
  return value;
}
export function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new Error('Invalid response');
  return value;
}
export function number(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw new Error('Invalid response');
  return value;
}
export function strings(value: unknown): string[] {
  if (!Array.isArray(value)) throw new Error('Invalid response');
  return value.map(string);
}
export function array<T>(value: unknown, decode: Decoder<T>): T[] {
  if (!Array.isArray(value)) throw new Error('Invalid response');
  return value.map(decode);
}
const nullableString = (value: unknown): string | null => (value === null ? null : string(value));

export interface Challenge {
  challenge_id: string;
  destination: string;
  resend_in: number;
  expires_in: number;
}
export function challenge(value: unknown): Challenge {
  const o = object(value);
  return {
    challenge_id: string(o.challenge_id),
    destination: string(o.destination),
    resend_in: number(o.resend_in),
    expires_in: number(o.expires_in),
  };
}
export interface Customer {
  id: string;
  full_name: string;
  email: string | null;
  email_verified: boolean;
  phone: string | null;
  phone_verified: boolean;
  has_password: boolean;
  google_linked: boolean;
}
export function customer(value: unknown): Customer {
  const o = object(value);
  return {
    id: string(o.id),
    full_name: string(o.full_name),
    email: nullableString(o.email),
    email_verified: boolean(o.email_verified),
    phone: nullableString(o.phone),
    phone_verified: boolean(o.phone_verified),
    has_password: boolean(o.has_password),
    google_linked: boolean(o.google_linked),
  };
}
export interface Methods {
  phone: boolean;
  google: boolean;
  phone_country: string;
}
export function methods(value: unknown): Methods {
  const o = object(value);
  return {
    phone: boolean(o.phone),
    google: boolean(o.google),
    phone_country: string(o.phone_country),
  };
}
export interface UserSummary {
  id: string;
  full_name: string;
  email: string | null;
}
export function userSummary(value: unknown): UserSummary {
  const o = object(value);
  return { id: string(o.id), full_name: string(o.full_name), email: nullableString(o.email) };
}
export interface StaffSession {
  user: UserSummary;
  role: string;
  role_label: string;
  permissions: string[];
  store_name: string;
}
export function staffSession(value: unknown): StaffSession {
  const o = object(value);
  return {
    user: userSummary(o.user),
    role: string(o.role),
    role_label: string(o.role_label),
    permissions: strings(o.permissions),
    store_name: string(o.store_name),
  };
}
export interface SecurityStatus {
  email: string | null;
  totp: boolean;
  email_codes: boolean;
  recovery_codes_remaining: number;
  reauthenticated: boolean;
}
export function securityStatus(value: unknown): SecurityStatus {
  const o = object(value);
  return {
    email: nullableString(o.email),
    totp: boolean(o.totp),
    email_codes: boolean(o.email_codes),
    recovery_codes_remaining: number(o.recovery_codes_remaining),
    reauthenticated: boolean(o.reauthenticated),
  };
}
export interface TotpSetup {
  qr_svg: string;
  secret: string;
}
export function totpSetup(value: unknown): TotpSetup {
  const o = object(value);
  const qr = string(o.qr_svg);
  if (!qr.startsWith('data:image/svg+xml;base64,')) throw new Error('Invalid QR code');
  return { qr_svg: qr, secret: string(o.secret) };
}
export function recoveryCodes(value: unknown): string[] | null {
  const codes = object(value).recovery_codes;
  return codes === null ? null : strings(codes);
}
export interface TeamMember {
  id: string;
  user: UserSummary;
  role: string;
  status: string;
  mfa_enabled: boolean;
}
export interface Invitation {
  id: string;
  email: string;
  role: string;
  expires_at: string;
}
export interface Role {
  value: string;
  label: string;
  description: string;
}
export interface Team {
  members: TeamMember[];
  invitations: Invitation[];
  roles: Role[];
}
export function team(value: unknown): Team {
  const o = object(value);
  return {
    members: array(o.members, (v) => {
      const m = object(v);
      return {
        id: string(m.id),
        user: userSummary(m.user),
        role: string(m.role),
        status: string(m.status),
        mfa_enabled: boolean(m.mfa_enabled),
      };
    }),
    invitations: array(o.invitations, (v) => {
      const i = object(v);
      return {
        id: string(i.id),
        email: string(i.email),
        role: string(i.role),
        expires_at: string(i.expires_at),
      };
    }),
    roles: array(o.roles, (v) => {
      const r = object(v);
      return { value: string(r.value), label: string(r.label), description: string(r.description) };
    }),
  };
}
