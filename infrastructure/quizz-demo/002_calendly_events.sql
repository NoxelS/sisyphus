BEGIN;
CREATE TABLE calendly_events (
  delivery_hash text PRIMARY KEY,
  event text NOT NULL CHECK (event IN ('invitee.created', 'invitee.canceled')),
  created_at timestamptz NOT NULL,
  invitee_uri text NOT NULL,
  email text,
  received_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (event, invitee_uri)
);
COMMIT;
