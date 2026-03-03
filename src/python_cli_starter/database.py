# src/python_cli_starter/database.py
import os
import logging
from datetime import date, datetime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base, Mapped, mapped_column
from sqlalchemy import String, Float, Integer, Date, DateTime, UniqueConstraint
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy import select, func
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# 获取数据库连接，自动将标准的 postgresql:// 转换为支持异步的 asyn·cpg://
db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/postgres")
if db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

engine = create_async_engine(db_url, echo=False)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)

Base = declarative_base()

# --- 东方财富板块表 ---
class EastMoneySector(Base):
    __tablename__ = "eastmoney_sectors"
    # 根据每天和板块名称建立唯一约束，用于实现当天的 upsert（首单新增，后续更新）
    __table_args__ = (UniqueConstraint('date', 'name', name='uix_eastmoney_date_name'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    market_cap: Mapped[float] = mapped_column(Float, nullable=False)
    market_cap_desc: Mapped[str] = mapped_column(String, nullable=False)
    turnover_rate: Mapped[float] = mapped_column(Float, nullable=False)
    turnover_rate_desc: Mapped[str] = mapped_column(String, nullable=False)
    change_percent: Mapped[float] = mapped_column(Float, nullable=False)
    change_percent_desc: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

# --- 同花顺板块表 ---
class ThsSector(Base):
    __tablename__ = "ths_sectors"
    __table_args__ = (UniqueConstraint('date', 'name', name='uix_ths_date_name'),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    change_percent: Mapped[float] = mapped_column(Float, nullable=False)
    net_inflow: Mapped[float] = mapped_column(Float, nullable=False)
    up_count: Mapped[int] = mapped_column(Integer, nullable=False)
    down_count: Mapped[int] = mapped_column(Integer, nullable=False)
    turnover_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

async def init_db():
    """初始化操作：不存在则自动创建 schema 与表"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("数据库及表结构初始化完成 (已具备时自动跳过)")

async def save_eastmoney_sectors(sectors):
    if not sectors: # 判空跳过
        return
    today = date.today()
    now = datetime.now()
    
    async with AsyncSessionLocal() as session:
        for sector in sectors:
            stmt = insert(EastMoneySector).values(
                date=today,
                name=sector.name,
                market_cap=sector.market_cap,
                market_cap_desc=sector.market_cap_desc,
                turnover_rate=sector.turnover_rate,
                turnover_rate_desc=sector.turnover_rate_desc,
                change_percent=sector.change_percent,
                change_percent_desc=sector.change_percent_desc,
                updated_at=now
            )
            # 冲突时进行更新
            stmt = stmt.on_conflict_do_update(
                index_elements=['date', 'name'],
                set_={
                    'market_cap': stmt.excluded.market_cap,
                    'market_cap_desc': stmt.excluded.market_cap_desc,
                    'turnover_rate': stmt.excluded.turnover_rate,
                    'turnover_rate_desc': stmt.excluded.turnover_rate_desc,
                    'change_percent': stmt.excluded.change_percent,
                    'change_percent_desc': stmt.excluded.change_percent_desc,
                    'updated_at': now
                }
            )
            await session.execute(stmt)
        await session.commit()
    logger.info(f"成功保存/更新 {len(sectors)} 条东方财富板块数据")

async def save_ths_sectors(sectors):
    if not sectors: # 判空跳过
        return
    today = date.today()
    now = datetime.now()
    
    async with AsyncSessionLocal() as session:
        for sector in sectors:
            stmt = insert(ThsSector).values(
                date=today,
                name=sector.name,
                change_percent=sector.change_percent,
                net_inflow=sector.net_inflow,
                up_count=sector.up_count,
                down_count=sector.down_count,
                turnover_ratio=sector.turnover_ratio,
                updated_at=now
            )
            # 冲突时进行更新
            stmt = stmt.on_conflict_do_update(
                index_elements=['date', 'name'],
                set_={
                    'change_percent': stmt.excluded.change_percent,
                    'net_inflow': stmt.excluded.net_inflow,
                    'up_count': stmt.excluded.up_count,
                    'down_count': stmt.excluded.down_count,
                    'turnover_ratio': stmt.excluded.turnover_ratio,
                    'updated_at': now
                }
            )
            await session.execute(stmt)
        await session.commit()
    logger.info(f"成功保存/更新 {len(sectors)} 条同花顺板块数据")

async def get_today_eastmoney_sectors():
    """获取数据库中最新一天的东方财富板块数据"""
    async with AsyncSessionLocal() as session:
        # 获取数据库中最新的日期，防止非交易日/周末查询不到数据
        subq = select(func.max(EastMoneySector.date))
        latest_date = await session.scalar(subq)
        if not latest_date:
            return[]
            
        stmt = select(EastMoneySector).where(EastMoneySector.date == latest_date).order_by(EastMoneySector.change_percent.desc())
        result = await session.execute(stmt)
        return result.scalars().all()

async def get_today_ths_sectors():
    """获取数据库中最新一天的同花顺板块数据"""
    async with AsyncSessionLocal() as session:
        # 获取数据库中最新的日期
        subq = select(func.max(ThsSector.date))
        latest_date = await session.scalar(subq)
        if not latest_date:
            return[]
            
        stmt = select(ThsSector).where(ThsSector.date == latest_date).order_by(ThsSector.change_percent.desc())
        result = await session.execute(stmt)
        return result.scalars().all()