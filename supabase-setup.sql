-- Калькулятор позиции: таблицы и правила доступа.
-- Вставьте целиком в Supabase → SQL Editor → New query и нажмите Run.
-- Каждый пользователь видит и меняет только свои данные (Row Level Security).

create table if not exists public.profiles (
  user_id    uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  data       jsonb  not null default '{}'::jsonb,
  updated_at bigint not null default 0
);

create table if not exists public.trades (
  user_id    uuid   not null default auth.uid() references auth.users(id) on delete cascade,
  id         text   not null,
  data       jsonb  not null default '{}'::jsonb,
  deleted    boolean not null default false,
  updated_at bigint not null default 0,
  primary key (user_id, id)
);

alter table public.profiles enable row level security;
alter table public.trades   enable row level security;

drop policy if exists "own profile" on public.profiles;
create policy "own profile" on public.profiles
  for all to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

drop policy if exists "own trades" on public.trades;
create policy "own trades" on public.trades
  for all to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

grant select, insert, update, delete on public.profiles to authenticated;
grant select, insert, update, delete on public.trades   to authenticated;
revoke all on public.profiles from anon;
revoke all on public.trades   from anon;
