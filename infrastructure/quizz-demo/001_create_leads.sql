BEGIN;

CREATE TABLE leads (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  eingegangen timestamptz NOT NULL DEFAULT now(),
  vorname text NOT NULL,
  nachname text,
  email text NOT NULL,
  ziel text NOT NULL,
  erfahrung text NOT NULL,
  zeit_pro_woche text NOT NULL,
  verletzungen boolean NOT NULL,
  akute_schmerzen boolean NOT NULL,
  laeufertyp text NOT NULL,
  newsletter boolean NOT NULL,
  einwilligung_am timestamptz NOT NULL DEFAULT now(),
  utm_source text,
  utm_campaign text,
  utm_content text
);

COMMIT;
