#include "AssetReaders/AssetReaderCommon.h"
#include "AssetReaders/AssetReaderImplementations.h"

namespace AssetReaderRegistryPrivate
{
	void AppendNiagaraRendererJson(
		const UNiagaraRendererProperties* Renderer,
		TArray<TSharedPtr<FJsonValue>>& OutRenderers)
	{
		if (Renderer == nullptr)
		{
			return;
		}
		TSharedRef<FJsonObject> RendererJson = MakeShared<FJsonObject>();
		RendererJson->SetStringField(TEXT("classPath"), Renderer->GetClass()->GetPathName());
		RendererJson->SetStringField(TEXT("objectName"), Renderer->GetName());
		RendererJson->SetBoolField(TEXT("enabled"), Renderer->GetIsEnabled());
		RendererJson->SetNumberField(TEXT("sortOrderHint"), Renderer->SortOrderHint);
		RendererJson->SetBoolField(TEXT("allowInCullProxies"), Renderer->bAllowInCullProxies);
		OutRenderers.Add(MakeShared<FJsonValueObject>(RendererJson));
	}

	TSharedRef<FJsonObject> NiagaraScriptToJson(const UNiagaraScript* Script)
	{
		TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
		Json->SetBoolField(TEXT("available"), Script != nullptr);
		if (Script != nullptr)
		{
			Json->SetStringField(TEXT("path"), Script->GetPathName());
			Json->SetStringField(TEXT("usage"), EnumNameOrValue<ENiagaraScriptUsage>(Script->GetUsage()));
			Json->SetNumberField(TEXT("usageValue"), static_cast<int32>(Script->GetUsage()));
			Json->SetStringField(TEXT("usageId"), Script->GetUsageId().ToString(EGuidFormats::DigitsWithHyphensLower));
		}
		return Json;
	}

	TArray<TSharedPtr<FJsonValue>> NiagaraParametersToJson(const FNiagaraParameterStore& Store)
	{
		TArray<FNiagaraVariable> Parameters;
		Store.GetParameters(Parameters);
		Parameters.Sort([](const FNiagaraVariable& Left, const FNiagaraVariable& Right)
		{
			const FString LeftKey = Left.GetName().ToString() + TEXT("|") + Left.GetType().GetName();
			const FString RightKey = Right.GetName().ToString() + TEXT("|") + Right.GetType().GetName();
			return LeftKey < RightKey;
		});

		TArray<TSharedPtr<FJsonValue>> Result;
		for (const FNiagaraVariable& Parameter : Parameters)
		{
			const FNiagaraTypeDefinition& Type = Parameter.GetType();
			TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
			Json->SetStringField(TEXT("name"), Parameter.GetName().ToString());
			Json->SetStringField(TEXT("type"), Type.GetName());
			Json->SetNumberField(TEXT("sizeBytes"), Parameter.GetSizeInBytes());
			Json->SetBoolField(TEXT("dataInterface"), Parameter.IsDataInterface());
			Json->SetBoolField(TEXT("uobject"), Parameter.IsUObject());
			Json->SetStringField(TEXT("objectPath"), FString());
			Json->SetStringField(TEXT("defaultValueHex"), FString());

			if (Parameter.IsDataInterface())
			{
				Json->SetStringField(TEXT("objectPath"), ObjectPathOrEmpty(Store.GetDataInterface(Parameter)));
			}
			else if (Parameter.IsUObject())
			{
				Json->SetStringField(TEXT("objectPath"), ObjectPathOrEmpty(Store.GetUObject(Parameter).Get()));
			}
			else if (Parameter.GetSizeInBytes() > 0)
			{
				const uint8* Data = Store.GetParameterData(Parameter);
				if (Data != nullptr)
				{
					Json->SetStringField(TEXT("defaultValueHex"), BytesToHex(Data, Parameter.GetSizeInBytes()));
				}
			}
			Result.Add(MakeShared<FJsonValueObject>(Json));
		}
		return Result;
	}

	EAssetReaderStatus ReadNiagaraSystem(
		const FAssetData& AssetData,
		TSharedRef<FJsonObject>& OutDetails,
		FString& OutError)
	{
		UNiagaraSystem* System = Cast<UNiagaraSystem>(AssetData.GetAsset());
		if (System == nullptr)
		{
			OutError = TEXT("Failed to load Niagara System asset.");
			return EAssetReaderStatus::Failed;
		}

		OutDetails->SetStringField(TEXT("type"), TEXT("niagara-system"));
		OutDetails->SetNumberField(TEXT("readerVersion"), 1);
		OutDetails->SetStringField(TEXT("effectTypePath"), ObjectPathOrEmpty(System->GetEffectType()));
		OutDetails->SetBoolField(TEXT("deterministic"), GetReflectedBool(System, TEXT("bDeterminism")));
		OutDetails->SetNumberField(TEXT("randomSeed"), System->GetRandomSeed());

		TSharedRef<FJsonObject> Warmup = MakeShared<FJsonObject>();
		Warmup->SetBoolField(TEXT("needed"), System->NeedsWarmup());
		Warmup->SetNumberField(TEXT("time"), System->GetWarmupTime());
		Warmup->SetNumberField(TEXT("tickCount"), System->GetWarmupTickCount());
		Warmup->SetNumberField(TEXT("tickDelta"), System->GetWarmupTickDelta());
		OutDetails->SetObjectField(TEXT("warmup"), Warmup);

		TSharedRef<FJsonObject> FixedTick = MakeShared<FJsonObject>();
		FixedTick->SetBoolField(TEXT("enabled"), System->HasFixedTickDelta());
		FixedTick->SetNumberField(TEXT("deltaTime"), System->GetFixedTickDeltaTime());
		const TOptional<float> MaxDeltaTime = System->GetMaxDeltaTime();
		FixedTick->SetBoolField(TEXT("hasMaxDeltaTime"), MaxDeltaTime.IsSet());
		FixedTick->SetNumberField(TEXT("maxDeltaTime"), MaxDeltaTime.Get(0.0f));
		OutDetails->SetObjectField(TEXT("fixedTick"), FixedTick);

		TSharedRef<FJsonObject> FixedBounds = BoxToJson(System->GetFixedBounds());
		FixedBounds->SetBoolField(TEXT("enabled"), GetReflectedBool(System, TEXT("bFixedBounds")));
		OutDetails->SetObjectField(TEXT("fixedBounds"), FixedBounds);
		OutDetails->SetObjectField(TEXT("systemSpawnScript"), NiagaraScriptToJson(System->GetSystemSpawnScript()));
		OutDetails->SetObjectField(TEXT("systemUpdateScript"), NiagaraScriptToJson(System->GetSystemUpdateScript()));

		TArray<TSharedPtr<FJsonValue>> ExposedParameters = NiagaraParametersToJson(System->GetExposedParameters());
		OutDetails->SetNumberField(TEXT("exposedParameterCount"), ExposedParameters.Num());
		OutDetails->SetArrayField(TEXT("exposedParameters"), ExposedParameters);

		TArray<TSharedPtr<FJsonValue>> Emitters;
		const TArray<FNiagaraEmitterHandle>& Handles = System->GetEmitterHandles();
		for (int32 HandleIndex = 0; HandleIndex < Handles.Num(); ++HandleIndex)
		{
			const FNiagaraEmitterHandle& Handle = Handles[HandleIndex];
			TSharedRef<FJsonObject> EmitterJson = MakeShared<FJsonObject>();
			EmitterJson->SetNumberField(TEXT("index"), HandleIndex);
			EmitterJson->SetStringField(TEXT("id"), Handle.GetId().ToString(EGuidFormats::DigitsWithHyphensLower));
			EmitterJson->SetStringField(TEXT("name"), Handle.GetName().ToString());
			EmitterJson->SetBoolField(TEXT("enabled"), Handle.GetIsEnabled());
			EmitterJson->SetStringField(TEXT("mode"), EnumNameOrValue<ENiagaraEmitterMode>(Handle.GetEmitterMode()));
			EmitterJson->SetNumberField(TEXT("modeValue"), static_cast<int32>(Handle.GetEmitterMode()));

			const FVersionedNiagaraEmitter Instance = Handle.GetInstance();
			EmitterJson->SetStringField(TEXT("emitterAssetPath"), ObjectPathOrEmpty(Instance.Emitter.Get()));
			EmitterJson->SetStringField(TEXT("version"), Instance.Version.ToString(EGuidFormats::DigitsWithHyphensLower));
			FVersionedNiagaraEmitterData* EmitterData = Handle.GetEmitterData();
			EmitterJson->SetBoolField(TEXT("emitterDataAvailable"), EmitterData != nullptr);

			TArray<TSharedPtr<FJsonValue>> RendererValues;
			TArray<TSharedPtr<FJsonValue>> ScriptValues;
			if (EmitterData != nullptr)
			{
				EmitterJson->SetBoolField(TEXT("localSpace"), EmitterData->bLocalSpace);
				EmitterJson->SetBoolField(TEXT("deterministic"), EmitterData->bDeterminism);
				EmitterJson->SetNumberField(TEXT("randomSeed"), EmitterData->RandomSeed);
				EmitterJson->SetStringField(TEXT("simTarget"), EnumNameOrValue<ENiagaraSimTarget>(EmitterData->SimTarget));
				EmitterJson->SetNumberField(TEXT("simTargetValue"), static_cast<int32>(EmitterData->SimTarget));
				EmitterJson->SetStringField(TEXT("boundsMode"), EnumNameOrValue<ENiagaraEmitterCalculateBoundMode>(EmitterData->CalculateBoundsMode));
				EmitterJson->SetNumberField(TEXT("boundsModeValue"), static_cast<int32>(EmitterData->CalculateBoundsMode));
				EmitterJson->SetObjectField(TEXT("fixedBounds"), BoxToJson(EmitterData->FixedBounds));
				EmitterJson->SetBoolField(TEXT("requiresPersistentIds"), EmitterData->RequiresPersistentIDs());
				EmitterJson->SetNumberField(TEXT("eventHandlerCount"), EmitterData->GetEventHandlers().Num());
				EmitterJson->SetNumberField(TEXT("simulationStageCount"), EmitterData->GetSimulationStages().Num());

				TArray<UNiagaraRendererProperties*> Renderers = EmitterData->GetRenderers();
				Renderers.Sort([](const UNiagaraRendererProperties& Left, const UNiagaraRendererProperties& Right)
				{
					const FString LeftKey = Left.GetClass()->GetPathName() + TEXT("|") + Left.GetName();
					const FString RightKey = Right.GetClass()->GetPathName() + TEXT("|") + Right.GetName();
					return LeftKey < RightKey;
				});
				for (const UNiagaraRendererProperties* Renderer : Renderers)
				{
					AppendNiagaraRendererJson(Renderer, RendererValues);
				}

				TArray<UNiagaraScript*> Scripts;
				EmitterData->GetScripts(Scripts, false, false);
				Scripts.Sort([](const UNiagaraScript& Left, const UNiagaraScript& Right)
				{
					const FString LeftKey = EnumNameOrValue<ENiagaraScriptUsage>(Left.GetUsage()) + TEXT("|") + Left.GetPathName();
					const FString RightKey = EnumNameOrValue<ENiagaraScriptUsage>(Right.GetUsage()) + TEXT("|") + Right.GetPathName();
					return LeftKey < RightKey;
				});
				for (const UNiagaraScript* Script : Scripts)
				{
					if (Script != nullptr)
					{
						ScriptValues.Add(MakeShared<FJsonValueObject>(NiagaraScriptToJson(Script)));
					}
				}
			}
			else
			{
				EmitterJson->SetBoolField(TEXT("localSpace"), false);
				EmitterJson->SetBoolField(TEXT("deterministic"), false);
				EmitterJson->SetNumberField(TEXT("randomSeed"), 0);
				EmitterJson->SetStringField(TEXT("simTarget"), FString());
				EmitterJson->SetNumberField(TEXT("simTargetValue"), 0);
				EmitterJson->SetStringField(TEXT("boundsMode"), FString());
				EmitterJson->SetNumberField(TEXT("boundsModeValue"), 0);
				EmitterJson->SetObjectField(TEXT("fixedBounds"), BoxToJson(FBox()));
				EmitterJson->SetBoolField(TEXT("requiresPersistentIds"), false);
				EmitterJson->SetNumberField(TEXT("eventHandlerCount"), 0);
				EmitterJson->SetNumberField(TEXT("simulationStageCount"), 0);
			}

			const bool bStateless = Handle.GetEmitterMode() == ENiagaraEmitterMode::Stateless;
			UObject* StatelessEmitter = bStateless
				? reinterpret_cast<UObject*>(Handle.GetStatelessEmitter())
				: nullptr;
			if (StatelessEmitter != nullptr && !IsValid(StatelessEmitter))
			{
				StatelessEmitter = nullptr;
			}
			EmitterJson->SetBoolField(TEXT("statelessEmitterAvailable"), StatelessEmitter != nullptr);
			EmitterJson->SetStringField(TEXT("statelessEmitterPath"), ObjectPathOrEmpty(StatelessEmitter));
			EmitterJson->SetStringField(TEXT("statelessEmitterClassPath"), StatelessEmitter != nullptr ? StatelessEmitter->GetClass()->GetPathName() : FString());

			TArray<TSharedPtr<FJsonValue>> StatelessModules;
			if (StatelessEmitter != nullptr)
			{
				EmitterJson->SetNumberField(TEXT("statelessSpawnInfoCount"), GetReflectedArrayCount(StatelessEmitter, TEXT("SpawnInfos")));
				for (const UObject* Module : GetReflectedObjectArray(StatelessEmitter, TEXT("Modules")))
				{
					TSharedRef<FJsonObject> ModuleJson = MakeShared<FJsonObject>();
					ModuleJson->SetStringField(TEXT("classPath"), Module->GetClass()->GetPathName());
					ModuleJson->SetStringField(TEXT("objectName"), Module->GetName());
					ModuleJson->SetBoolField(TEXT("enabled"), GetReflectedBool(Module, TEXT("bModuleEnabled"), true));
					StatelessModules.Add(MakeShared<FJsonValueObject>(ModuleJson));
				}
				EmitterJson->SetBoolField(TEXT("deterministic"), GetReflectedBool(StatelessEmitter, TEXT("bDeterministic")));
				EmitterJson->SetNumberField(TEXT("randomSeed"), GetReflectedInt(StatelessEmitter, TEXT("RandomSeed")));
				EmitterJson->SetObjectField(TEXT("fixedBounds"), GetReflectedBox(StatelessEmitter, TEXT("FixedBounds")));
				RendererValues.Reset();
				TArray<UNiagaraRendererProperties*> StatelessRenderers;
				for (UObject* Object : GetReflectedObjectArray(StatelessEmitter, TEXT("RendererProperties")))
				{
					if (UNiagaraRendererProperties* Renderer = Cast<UNiagaraRendererProperties>(Object))
					{
						StatelessRenderers.Add(Renderer);
					}
				}
				StatelessRenderers.Sort([](const UNiagaraRendererProperties& Left, const UNiagaraRendererProperties& Right)
				{
					const FString LeftKey = Left.GetClass()->GetPathName() + TEXT("|") + Left.GetName();
					const FString RightKey = Right.GetClass()->GetPathName() + TEXT("|") + Right.GetName();
					return LeftKey < RightKey;
				});
				for (const UNiagaraRendererProperties* Renderer : StatelessRenderers)
				{
					AppendNiagaraRendererJson(Renderer, RendererValues);
				}
			}
			else
			{
				EmitterJson->SetNumberField(TEXT("statelessSpawnInfoCount"), 0);
			}
			EmitterJson->SetNumberField(TEXT("statelessModuleCount"), StatelessModules.Num());
			EmitterJson->SetArrayField(TEXT("statelessModules"), StatelessModules);

			EmitterJson->SetNumberField(TEXT("rendererCount"), RendererValues.Num());
			EmitterJson->SetArrayField(TEXT("renderers"), RendererValues);
			EmitterJson->SetNumberField(TEXT("scriptCount"), ScriptValues.Num());
			EmitterJson->SetArrayField(TEXT("scripts"), ScriptValues);
			Emitters.Add(MakeShared<FJsonValueObject>(EmitterJson));
		}
		OutDetails->SetNumberField(TEXT("emitterCount"), Emitters.Num());
		OutDetails->SetArrayField(TEXT("emitters"), Emitters);
		return EAssetReaderStatus::Success;
	}
}
