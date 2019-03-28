
using CSV, DataFrames, Dates, Logging
using LinearAlgebra
using JuMP, GLPK, Gurobi
const MOI = JuMP.MathOptInterface

global suppress_csv_write = Dict("backup" => true,
								 "final" => false)

global_logger(ConsoleLogger(stdout, Logging.Info))

if length(ARGS) == 0
    global wdir = pwd()
	@info("No arguments passed or not running in repl, initializing in pwd(): ", wdir)
end

function is_redundant(model::JuMP.Model, constraint::Vector{Float64}, rhs::Float64)
	temp = @constraint(model, constraint' * model[:x] <= rhs + 1)
	@objective(model, Max, constraint' * model[:x])
	JuMP.optimize!(model)
	JuMP.delete(model, temp)
	if JuMP.objective_value(model) > rhs
		result = []
		for i in model[:x]
			push!(result, JuMP.value(i))
		end
		return true, result, model
	else
		return false, [], model
	end
end

function build_model(n::Int, A::Array{Float64}, b::Vector{Float64})
	# model = Model(with_optimizer(GLPK.Optimizer))
	model = Model(with_optimizer(Gurobi.Optimizer, OutputFlag=0)) #, Presolve=0)) #, Method=2))
	@variable(model, x[i=1:n])
	@constraint(model, A * x .<= b)
	return model
end

function add_constraint(model::JuMP.Model, constraint::Vector{Float64}, rhs::Float64)
	@constraint(model, sum(constraint .* model[:x]) <= rhs)
	return model
end

# function add_constraint(model::JuMP.Model, constraint::Array{Float64}, rhs::Vector{Float64})
# 	@constraint(model, sum(constraint * model[:x]) .<= rhs)
# 	return model
# end

function RayShoot(A::Array{Float64}, b::Vector{Float64}, m::Vector{Int},
	              z::Vector{Float64}, r::Vector{Float64})
	stepsize = 1
	i = 0
	counter = 0
	while true
		point = z + r*i
		temp = A[m,:]*point - b[m]
		# If 1 constraint is hit, return it
		if size(findall(x-> x>0, temp), 1) == 1
			@debug("Constraint hit: $(m[findall(x->x>0, temp)]) With i = $i")
			return m[findfirst(x->x>0, temp)]
		# If more than one constraint is hit:
		# 1) Check for breaking condition:
		#	 If True return either the first of the constraints hit
		#	 or all (currently only the first is returnd, since the others
		#	 will be checked at a lated stage since they will remain in m)
		#
		# 2) 1) Go one step back, to where no constraint was hit
		#    2) Continue forward with 1/of stepsize
		#	 3) Increase counter, since this procedure is limited by
		#	    17 decimal places for increment i
		elseif size(findall(x-> x>0, temp))[1] > 1
			# Check breaking condition
			if counter > 12
				@debug("Counter > 10, returning first of the constraints hit!")
				@debug("Constraints hit: $(m[findall(x->x>0, temp)]) With i = $i")
				return m[findfirst(x->x>0, temp)]
				# return m[findall(x->x>0, temp)]
			else
				counter += 1
				# #println("Going back and reducing stepsize. counter = ", counter)
				i = i - exp10(-stepsize)
				stepsize += 1
			end
		# No constraint is hit, incease increment i by stepsize
		else
			i = i + exp10(-stepsize)
		end
	end
end

function main(A::Array{Float64}, b::Vector{Float64},
			  m::Vector{Int}, I::Vector{Int}, z::Vector{Float64})

	@info("Starting Algorithm with I of size: $(length(I))")
	@info("and with m of size: $(length(m))")
	# Set-up
	# Make counter to print out progress and make backups
	# every number of steps
	steps = 100
	to_check = length(m)
	stepsize = round(to_check/steps)
	save_points = [Int(x) for x in stepsize:stepsize:to_check]
	# Make sure only 100 save_points are available, remove the first ones
	if length(save_points) > 100
		save_points = save_points[(length(save_points) + 1 - 100):end]
	end
	### Build base model
	model = build_model(size(A, 2), A[I,:], b[I])
	# Start Algorithm
	while true
		k = m[1]
		@debug("checking constraint k = $k")
		# Check redundancy of constraint k
		alpha, x_opt = is_redundant(model, A[k,:], b[k])
		if alpha
			# If true, rayshoot and add constraint j to the model
			j = RayShoot(A, b, m, z, x_opt-z)
			@debug("k = $k and j = $j")
			model = add_constraint(model, A[j,:], b[j])
			m = setdiff(m, j)
			I = union(I, j)
		else
			# if not, remove constraint from m
			m = setdiff(m, k)
		end
		# print progress and make backups of I, to allow for restart incase of crash :(
		if length(m) in save_points
			percentage = Int(100 - 100/steps*findfirst(x -> x==length(m), save_points))
			progress_bar = repeat("#", Int(round(percentage/5)))*repeat(" ", Int(round((100-percentage)/5)))
			@info(progress_bar*string(percentage)*"% done!")
			save_to_file(I, "backups/cbco_backup_"*string(percentage), suppress_csv_write["backup"])
		end
		if length(m) == 0
			break
		end
	end
	return I
end

function save_to_file(Indices, filename::String, suppress_write::Bool=false)
	# I_result.-1: Indices start at 0 (in python.... or any other decent programming language)
	if !suppress_write
		@info("Writing File "*filename*" .... ")
		CSV.write(wdir*"/data_temp/julia_files/cbco_data/"*filename*".csv",
		  	 	  DataFrame(constraints = Indices.-1))
		@info("done! ")
	else
		@debug("No File Written bc of debug parameter in Line 8")
	end
end

function read_data(file_suffix::String)
	# Read Data From CSV Files
	@info("Reading A, b Matrices...")
	A_data = CSV.read(wdir*"/data_temp/julia_files/cbco_data/A_"*file_suffix*".csv", 
					  delim=',', header=false)
	b_data = CSV.read(wdir*"/data_temp/julia_files/cbco_data/b_"*file_suffix*".csv", 
					  delim=',', header=false, types=Dict(1=>Float64))

	A =  hcat([A_data[i] for i in 1:size(A_data, 2)]...)
	if size(b_data, 2) > 0
		b = b_data[1]
	else 
		b = []
	end
	return A, b
end

function run(file_suffix::String)
	I = Array{Int, 1}()
	A, b = read_data(file_suffix)
	A_base, b_base = read_data("base_"*file_suffix)
	
	if length(b_base) == 0
		A_base, b_base = A[I,:], b[I]
	end

	m = collect(range(1, stop=length(b) + length(b_base)))
	# Adding base problem
	index_loadflow = collect(range(1, stop=length(b)))
	index_base = collect(range(length(b), stop=length(b) + length(b_base)))
	A = vcat(A, A_base) 
	b = vcat(b, b_base)
	@info("Preprocessing...")
	@info("Removing duplicate rows...")
	# Remove douplicates
	condition_unique = .!nonunique(DataFrame(hcat(A,b)))
	@info("Removing all zero rows...")
	# Remove cb = co rows
	condition_zero = vcat([!all(A[i, :] .== 0) for i in 1:length(b)])
	m = m[condition_unique .& condition_zero]
	@info("Removed $(length(b) + length(b_base) - length(m)) rows in preprocessing!")
	z = zeros(size(A, 2))
	
	I = index_base
	m = setdiff(m, I)
	I_full = main(A, b, m, I, z)
	I_result = [cbco for cbco in I_full if cbco in index_loadflow]

	@info("Number of non-redundant constraints: $(length(I_result))" )
	save_to_file(I_result, "cbco_"*file_suffix*"_"*Dates.format(now(), "ddmm_HHMM"), 
		         suppress_csv_write["final"])
end

function run_with_I(file_suffix::String, I_file::String, start_index::Int=1)
	A, b = read_data(file_suffix)
	I_data = CSV.read(wdir*"/data_temp/julia_files/cbco_data/"*I_file*".csv",
					  delim=',', types=Dict(1=>Int))
	
	if length(I_data) > 0
  		I = I_data[1].+1
  	else
  		I = Array{Int, 1}()
  	end
	@info("Preprocessing...")
	@info("Removing duplicate rows...")
	# Remove douplicates
	condition_unique = .!nonunique(DataFrame(hcat(A,b)))
	@info("Removing all zero rows...")
	# Remove cb = co rows
	condition_zero = vcat([!all(A[i, :] .== 0) for i in 1:length(b)])

	m = collect(range(1, length(b)))
	m = m[condition_unique .& condition_zero]
	m = filter(x -> x >= start_index, m)

	@info("Removed $(length(b) - length(m)) rows in preprocessing!")
	m = setdiff(m, I)

	z = zeros(size(A, 2))

	I_result = @time main(A, b, m, I, z)

	@info("Number of non-redundant constraints: $(length(I_result))")
	save_to_file(I_result, "cbco_I_"*file_suffix*"_"*Dates.format(now(), "ddmm_HHMM"), 
				 suppress_csv_write["final"])
end

### Make it rum from repl
if length(ARGS) > 0
	file_suffix = ARGS[1]
	global wdir = ARGS[2]
	global_logger(ConsoleLogger(stdout, Logging.Info))
    @time run(file_suffix)
end
